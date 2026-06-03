import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx


class ModelService:
    def start_sandbox_service(
        self,
        model_service_type: str = "local",
        config_file: str | None = None,
        host: str | None = None,
        port: int | None = None,
        proxy_base_url: str | None = None,
        retryable_status_codes: str | None = None,
        request_timeout: int | None = None,
        recording_file: str | None = None,
        replay_file: str | None = None,
        uts_recording_dir: str | None = None,
        uts_source: str | None = None,
        uts_scaffold: str | None = None,
        uts_channel: str | None = None,
        uts_trace_id: str | None = None,
        uts_session_id: str | None = None,
    ) -> subprocess.Popen:
        """start sandbox service"""
        current_file = Path(__file__).resolve()
        service_dir = current_file.parent / "server"

        if not service_dir.exists():
            raise FileNotFoundError(f"Service directory not found: {service_dir}")

        cmd = [sys.executable, "-m", "main", "--type", model_service_type]
        if config_file:
            cmd.extend(["--config-file", config_file])
        if host:
            cmd.extend(["--host", host])
        if port:
            cmd.extend(["--port", str(port)])
        if proxy_base_url:
            cmd.extend(["--proxy-base-url", proxy_base_url])
        if retryable_status_codes:
            cmd.extend(["--retryable-status-codes", retryable_status_codes])
        if request_timeout:
            cmd.extend(["--request-timeout", str(request_timeout)])
        if recording_file:
            cmd.extend(["--recording-file", recording_file])
        if replay_file:
            cmd.extend(["--replay-file", replay_file])
        if uts_recording_dir:
            cmd.extend(["--uts-recording-dir", uts_recording_dir])
        if uts_source:
            cmd.extend(["--uts-source", uts_source])
        if uts_scaffold:
            cmd.extend(["--uts-scaffold", uts_scaffold])
        if uts_channel:
            cmd.extend(["--uts-channel", uts_channel])
        if uts_trace_id:
            cmd.extend(["--uts-trace-id", uts_trace_id])
        if uts_session_id:
            cmd.extend(["--uts-session-id", uts_session_id])
        process = subprocess.Popen(cmd, cwd=str(service_dir))
        return process

    async def start(
        self,
        timeout_seconds: int = 30,
        model_service_type: str = "local",
        config_file: str | None = None,
        host: str | None = None,
        port: int | None = None,
        proxy_base_url: str | None = None,
        retryable_status_codes: str | None = None,
        request_timeout: int | None = None,
        recording_file: str | None = None,
        replay_file: str | None = None,
        uts_recording_dir: str | None = None,
        uts_source: str | None = None,
        uts_scaffold: str | None = None,
        uts_channel: str | None = None,
        uts_trace_id: str | None = None,
        uts_session_id: str | None = None,
    ) -> str:
        process = self.start_sandbox_service(
            model_service_type=model_service_type,
            config_file=config_file,
            host=host,
            port=port,
            proxy_base_url=proxy_base_url,
            retryable_status_codes=retryable_status_codes,
            request_timeout=request_timeout,
            recording_file=recording_file,
            replay_file=replay_file,
            uts_recording_dir=uts_recording_dir,
            uts_source=uts_source,
            uts_scaffold=uts_scaffold,
            uts_channel=uts_channel,
            uts_trace_id=uts_trace_id,
            uts_session_id=uts_session_id,
        )
        pid = process.pid

        success = await self._wait_service_available(timeout_seconds, host or "127.0.0.1", port or 8080)
        if not success:
            await self.stop(str(pid))
            raise Exception("Model service start failed")

        return str(pid)

    async def start_watch_agent(self, agent_pid: int, host: str = "127.0.0.1", port: int = 8080):
        async with httpx.AsyncClient() as client:
            await client.post(f"http://{host}:{port}/v1/agent/watch", json={"pid": agent_pid})

    async def stop(self, pid: str):
        subprocess.run(["kill", "-9", pid])

    async def _wait_service_available(self, timeout_seconds: int, host: str = "127.0.0.1", port: int = 8080) -> bool:
        start = datetime.now()
        while (datetime.now() - start).seconds < timeout_seconds:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"http://{host}:{port}/health")
                    if response.status_code == 200:
                        return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        return False
