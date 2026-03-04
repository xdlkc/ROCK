"""
Cycle 6 — RED: AuditWriter 异步写入与降级行为测试。
"""

import asyncio
import json
import os
import tempfile

import pytest

from rock.egress.audit_writer import AuditWriter
from rock.egress.models import AuditMode, AuditRecord


@pytest.fixture
def tmp_log_dir(tmp_path):
    return str(tmp_path)


class TestAuditWriterBasicWrite:
    async def test_write_record_creates_file(self, tmp_log_dir):
        writer = AuditWriter(log_dir=tmp_log_dir, queue_maxsize=100)
        await writer.start()

        record = AuditRecord(sandbox_id="sbx-001", host="api.openai.com", status_code=200)
        await writer.write(record)

        # 等待后台任务刷盘
        await asyncio.sleep(0.2)
        await writer.stop()

        # 验证文件存在且内容正确
        log_files = []
        for root, _, files in os.walk(tmp_log_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    log_files.append(os.path.join(root, f))
        assert len(log_files) == 1

        with open(log_files[0]) as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["sandbox_id"] == "sbx-001"
        assert data["host"] == "api.openai.com"
        assert data["status_code"] == 200

    async def test_write_multiple_records(self, tmp_log_dir):
        writer = AuditWriter(log_dir=tmp_log_dir, queue_maxsize=100)
        await writer.start()

        for i in range(5):
            await writer.write(AuditRecord(sandbox_id=f"sbx-{i:03d}"))

        await asyncio.sleep(0.2)
        await writer.stop()

        log_files = []
        for root, _, files in os.walk(tmp_log_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    log_files.append(os.path.join(root, f))

        total_lines = 0
        for path in log_files:
            with open(path) as f:
                total_lines += sum(1 for l in f if l.strip())
        assert total_lines == 5


class TestAuditWriterNonBlocking:
    async def test_queue_full_drops_record_without_blocking(self, tmp_log_dir):
        """队列满时 write() 应立即返回（不阻塞），多余记录被丢弃。"""
        writer = AuditWriter(log_dir=tmp_log_dir, queue_maxsize=2)
        # 不启动消费者，让队列堆满
        await writer.start()
        # 暂停消费者以模拟写入阻塞场景
        writer._pause_consumer = True

        await asyncio.sleep(0.05)

        # 写入超过队列容量的记录，不应阻塞
        start = asyncio.get_event_loop().time()
        for _ in range(10):
            await writer.write(AuditRecord(sandbox_id="sbx-drop"))
        elapsed = asyncio.get_event_loop().time() - start

        # write 操作总时间应极短（非阻塞）
        assert elapsed < 0.5, f"write() blocked for {elapsed:.3f}s, should be non-blocking"

        await writer.stop()

    async def test_drop_counter_incremented_on_full_queue(self, tmp_log_dir):
        """队列满时丢弃计数器应递增。"""
        writer = AuditWriter(log_dir=tmp_log_dir, queue_maxsize=1)
        await writer.start()
        writer._pause_consumer = True
        await asyncio.sleep(0.05)

        for _ in range(5):
            await writer.write(AuditRecord(sandbox_id="sbx-cnt"))

        drop_count = writer.drop_count
        assert drop_count > 0

        await writer.stop()


class TestAuditWriterWriteFailure:
    async def test_write_failure_does_not_raise(self, tmp_log_dir):
        """日志写入失败时不应向调用方抛异常（优先保证转发路径）。"""
        writer = AuditWriter(log_dir="/nonexistent/path/that/cannot/be/created", queue_maxsize=10)
        await writer.start()

        # 不应抛异常
        await writer.write(AuditRecord(sandbox_id="sbx-fail"))
        await asyncio.sleep(0.2)

        failure_count = writer.write_failure_count
        assert failure_count > 0

        await writer.stop()


class TestAuditWriterMetadataOnlyFiltering:
    async def test_metadata_only_strips_body_fields(self, tmp_log_dir):
        """metadata-only 模式下，body 相关字段不写入日志。"""
        writer = AuditWriter(log_dir=tmp_log_dir, queue_maxsize=100)
        await writer.start()

        record = AuditRecord(
            sandbox_id="sbx-meta",
            audit_mode=AuditMode.METADATA_ONLY.value,
            request_body='{"model": "gpt-4"}',
            response_body='{"id": "chatcmpl-xxx"}',
            request_headers={"Authorization": "Bearer sk-****"},
            response_headers={"content-type": "application/json"},
        )
        await writer.write(record)
        await asyncio.sleep(0.2)
        await writer.stop()

        log_files = []
        for root, _, files in os.walk(tmp_log_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    log_files.append(os.path.join(root, f))

        with open(log_files[0]) as f:
            data = json.loads(f.readline())

        assert data.get("request_body") is None
        assert data.get("response_body") is None
        assert data.get("request_headers") is None
        assert data.get("response_headers") is None
