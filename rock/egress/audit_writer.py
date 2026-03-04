import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

from rock.egress.models import AuditMode, AuditRecord
from rock.logger import init_logger

logger = init_logger(__name__)


class AuditWriter:
    """
    异步审计日志写入器。

    - 以有界 asyncio.Queue 作为缓冲区，写入者永不阻塞。
    - 队列满时：丢弃新记录，递增 drop_count，不阻断转发路径。
    - 日志写入失败时：递增 write_failure_count，不向调用方抛出异常。
    - metadata-only 模式下过滤 body 相关字段，避免明文落盘。

    日志格式：JSON Lines（每行一条 AuditRecord）。
    文件路径：{log_dir}/{YYYY-MM-DD}/audit.jsonl
    """

    def __init__(self, log_dir: str, queue_maxsize: int = 10000) -> None:
        self._log_dir = log_dir
        self._queue: asyncio.Queue[Optional[AuditRecord]] = asyncio.Queue(maxsize=queue_maxsize)
        self._consumer_task: Optional[asyncio.Task] = None
        self.drop_count: int = 0
        self.write_failure_count: int = 0
        # 测试辅助：暂停消费者模拟背压
        self._pause_consumer: bool = False

    async def start(self) -> None:
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        self._pause_consumer = False
        await self._queue.put(None)  # 终止信号
        if self._consumer_task:
            await asyncio.wait_for(self._consumer_task, timeout=5.0)

    async def write(self, record: AuditRecord) -> None:
        """非阻塞写入：队列满时立即丢弃并记录计数。"""
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.drop_count += 1
            logger.warning(
                f"AuditWriter: queue full, dropping record for sandbox={record.sandbox_id}. "
                f"total_drops={self.drop_count}"
            )

    async def _consume(self) -> None:
        while True:
            if self._pause_consumer:
                await asyncio.sleep(0.01)
                continue

            try:
                record = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            if record is None:
                break

            await self._flush(record)
            self._queue.task_done()

    async def _flush(self, record: AuditRecord) -> None:
        try:
            log_path = self._log_path()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            line = json.dumps(self._to_log_dict(record), ensure_ascii=False) + "\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            self.write_failure_count += 1
            logger.error(f"AuditWriter: failed to write audit record: {e}")

    def _log_path(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(self._log_dir, date_str, "audit.jsonl")

    @staticmethod
    def _to_log_dict(record: AuditRecord) -> dict:
        d = record.to_dict()
        # metadata-only 模式下不落盘 body/headers 内容
        if record.audit_mode != AuditMode.FULL_CAPTURE.value:
            for key in ("request_body", "response_body", "request_headers", "response_headers"):
                d.pop(key, None)
        return d
