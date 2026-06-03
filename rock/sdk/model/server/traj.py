"""Trajectory record + replay for the chat/completions proxy.

Two halves around the same JSONL schema (one record per line):

- :class:`TrajectoryRecorder` — invoked by the forward path after each upstream
  call (success or failure). Appends a small dict with
  ``request`` / ``response`` / ``status`` / ``response_time`` / ``model`` /
  ``stream``, and reports OTLP RT/count metrics. Stores responses verbatim
  (provider-specific fields like ``reasoning_content`` survive); for streaming
  calls ``response`` is the aggregated final ChatCompletion produced by
  ``ChatCompletionStreamState.get_final_completion().model_dump()``.

- :class:`SequentialCursor` — loads a JSONL trajectory once at startup;
  ``await cursor.next(expected_model=...)`` hands out the next record (full
  payload dict) and advances. Going past the end raises
  :class:`TrajectoryExhausted` so the proxy can return a clean 404.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rock.logger import init_logger
from rock.sdk.model.server.utils import (
    MODEL_SERVICE_REQUEST_COUNT,
    MODEL_SERVICE_REQUEST_RT,
    _get_or_create_metrics_monitor,
)

logger = init_logger(__name__)

UTC_PLUS_8 = timezone(timedelta(hours=8))


def _format_utc8_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")


def _json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _count_messages(request: dict[str, Any]) -> dict[str, int | None]:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return {
            "total_turns": None,
            "user_turns": None,
            "assistant_turns": None,
            "tool_calls_count": None,
        }

    tool_calls_count = 0
    user_turns = 0
    assistant_turns = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            user_turns += 1
        elif role == "assistant":
            assistant_turns += 1
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_calls_count += len(tool_calls)

    return {
        "total_turns": len(messages),
        "user_turns": user_turns,
        "assistant_turns": assistant_turns,
        "tool_calls_count": tool_calls_count,
    }


def _usage_tokens(response: dict[str, Any] | None) -> dict[str, int | None]:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return {
            "token_input": None,
            "token_output": None,
            "token_cache_read": None,
            "token_cache_create": None,
            "total_tokens": None,
        }

    token_input = usage.get("prompt_tokens", usage.get("input_tokens"))
    token_output = usage.get("completion_tokens", usage.get("output_tokens"))
    prompt_details = usage.get("prompt_tokens_details")
    token_cache_read = None
    if isinstance(prompt_details, dict):
        token_cache_read = prompt_details.get("cached_tokens")
    token_cache_read = usage.get("cache_read_input_tokens", token_cache_read)
    token_cache_create = usage.get("cache_creation_input_tokens")
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        parts = [token_input, token_output, token_cache_read, token_cache_create]
        total_tokens = sum(p for p in parts if isinstance(p, int)) if any(isinstance(p, int) for p in parts) else None

    return {
        "token_input": token_input if isinstance(token_input, int) else None,
        "token_output": token_output if isinstance(token_output, int) else None,
        "token_cache_read": token_cache_read if isinstance(token_cache_read, int) else None,
        "token_cache_create": token_cache_create if isinstance(token_cache_create, int) else None,
        "total_tokens": total_tokens if isinstance(total_tokens, int) else None,
    }


class UatfRecorder:
    """Writes UTS/UATF-compatible call records partitioned by ds/channel."""

    def __init__(
        self,
        root_dir: str | os.PathLike,
        *,
        source: str = "rock-model-service",
        scaffold: str | None = "rock-proxy",
        channel: str = "collect",
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.source = source
        self.scaffold = scaffold
        self.channel = channel
        self.trace_id = trace_id
        self.session_id = session_id
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        status: str,
        start_time: float,
        end_time: float,
        error: str | None = None,
    ) -> None:
        started_at = _format_utc8_timestamp(start_time)
        finished_at = _format_utc8_timestamp(end_time)
        ds = started_at[:10].replace("-", "")
        request_id = response.get("id") if isinstance(response, dict) else None
        model = request.get("model") or (response or {}).get("model") or "unknown"
        raw_response: dict[str, Any] = response if response is not None else {"status": status, "error": error}
        cleaned_response = response if status == "success" and response is not None else None
        extend_tags = {
            "status": status,
            "response_time": end_time - start_time,
            "error": error,
            "rock_sandbox_id": os.getenv("ROCK_SANDBOX_ID"),
        }
        extend_tags = {k: v for k, v in extend_tags.items() if v is not None}
        id_parts = [self.trace_id or "", self.session_id or "", request_id or "", started_at]
        record = {
            "schema_version": "UATF-v1.0",
            "id": hashlib.md5("|".join(id_parts).encode()).hexdigest(),
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "request_id": request_id,
            "user_id": None,
            "device_id": None,
            "unified_id": None,
            "source": self.source,
            "scaffold": self.scaffold,
            "raw_protocol": "openai_chat_completions",
            "task_type": "LLM",
            "model_original": str(model),
            "model_normalize": str(model),
            "raw_request": _json_string(request),
            "raw_response": _json_string(raw_response),
            "cleaned_request": _json_string(request),
            "cleaned_response": _json_string(cleaned_response) if cleaned_response is not None else None,
            **_count_messages(request),
            **_usage_tokens(response),
            "started_at": started_at,
            "finished_at": finished_at,
            "modality_tags": _json_string({"stream": bool(request.get("stream"))}),
            "inference_tags": "{}",
            "extend_tags": _json_string(extend_tags),
            "ds": ds,
            "channel": self.channel,
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        file_path = self.root_dir / ds / f"{self.channel}.jsonl"
        async with self._lock:
            await asyncio.to_thread(self._write_line, file_path, line)

    @staticmethod
    def _write_line(file_path: Path, line: str) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as f:
            f.write(line)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class TrajectoryRecorder:
    """Appends one JSONL line per chat/completions call and reports OTLP metrics."""

    def __init__(self, traj_file: str | os.PathLike, uatf_recorder: UatfRecorder | None = None) -> None:
        self.traj_file = Path(traj_file)
        self.traj_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._monitor = _get_or_create_metrics_monitor()
        self._uatf_recorder = uatf_recorder

    async def record(
        self,
        *,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        status: str,
        start_time: float,
        end_time: float,
        error: str | None = None,
    ) -> None:
        rt_seconds = end_time - start_time
        payload = {
            "model": request.get("model"),
            "stream": bool(request.get("stream")),
            "status": status,
            "response_time": rt_seconds,
            "start_time": start_time,
            "end_time": end_time,
            "request": request,
            "response": response,
            "error": error,
        }

        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write_line, line)

        if self._uatf_recorder is not None:
            await self._uatf_recorder.record(
                request=request,
                response=response,
                status=status,
                start_time=start_time,
                end_time=end_time,
                error=error,
            )

        attrs = {
            "type": "chat_completions",
            "status": status,
            "sandbox_id": os.getenv("ROCK_SANDBOX_ID", "unknown"),
        }
        self._monitor.record_gauge_by_name(MODEL_SERVICE_REQUEST_RT, rt_seconds * 1000.0, attributes=attrs)
        self._monitor.record_counter_by_name(MODEL_SERVICE_REQUEST_COUNT, 1, attributes=attrs)

    def _write_line(self, line: str) -> None:
        with self.traj_file.open("a", encoding="utf-8") as f:
            f.write(line)


# ---------------------------------------------------------------------------
# Replay cursor
# ---------------------------------------------------------------------------


class TrajectoryExhausted(Exception):
    """Raised by ``SequentialCursor.next`` when all recorded steps have been served."""

    def __init__(self, position: int, total: int) -> None:
        super().__init__(f"trajectory exhausted at step {position} (total recorded steps={total})")
        self.position = position
        self.total = total


class SequentialCursor:
    """Hands out trajectory records one at a time, in recorded order."""

    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self._idx = 0
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, path: str | os.PathLike) -> SequentialCursor:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"traj file not found: {path}")

        records: list[dict] = []
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))

        logger.info(f"[traj-replay] loaded {len(records)} record(s) from {path}")
        return cls(records)

    async def next(self, expected_model: str | None = None) -> dict:
        async with self._lock:
            if self._idx >= len(self.records):
                raise TrajectoryExhausted(position=self._idx, total=len(self.records))
            record = self.records[self._idx]
            self._idx += 1
            current_idx = self._idx - 1

        if expected_model:
            recorded_model = record.get("model")
            if recorded_model and recorded_model != expected_model:
                logger.warning(
                    f"[traj-replay] step {current_idx} model mismatch: "
                    f"recorded={recorded_model!r} requested={expected_model!r}"
                )
        return record

    def reset(self) -> None:
        self._idx = 0

    @property
    def position(self) -> int:
        return self._idx

    @property
    def total(self) -> int:
        return len(self.records)
