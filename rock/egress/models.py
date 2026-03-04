import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EgressErrorCode(str, Enum):
    POLICY_DENIED = "EGRESS_POLICY_DENIED"
    UPSTREAM_TIMEOUT = "EGRESS_UPSTREAM_TIMEOUT"
    UPSTREAM_UNREACHABLE = "EGRESS_UPSTREAM_UNREACHABLE"
    INTERNAL_ERROR = "EGRESS_GATEWAY_INTERNAL_ERROR"

    def to_http_status(self) -> int:
        mapping = {
            EgressErrorCode.POLICY_DENIED: 403,
            EgressErrorCode.UPSTREAM_TIMEOUT: 504,
            EgressErrorCode.UPSTREAM_UNREACHABLE: 502,
            EgressErrorCode.INTERNAL_ERROR: 500,
        }
        return mapping[self]

    def is_retryable(self) -> bool:
        return self in (EgressErrorCode.UPSTREAM_TIMEOUT, EgressErrorCode.UPSTREAM_UNREACHABLE)


class AuditMode(str, Enum):
    OFF = "off"
    METADATA_ONLY = "metadata-only"
    FULL_CAPTURE = "full-capture"


class IdentitySource(str, Enum):
    NETWORK_MAPPING = "network_mapping"
    HEADER = "header"
    MIXED = "mixed"


@dataclass
class SandboxIdentity:
    sandbox_id: str
    user_id: str = ""
    experiment_id: str = ""
    namespace: str = ""
    identity_source: str = IdentitySource.NETWORK_MAPPING.value
    identity_verified: bool = True


def _new_request_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditRecord:
    request_id: str = field(default_factory=_new_request_id)
    trace_id: str = ""
    sandbox_id: str = ""
    user_id: str = ""
    experiment_id: str = ""
    namespace: str = ""
    identity_source: str = IdentitySource.NETWORK_MAPPING.value
    identity_verified: bool = True

    timestamp: str = field(default_factory=_now_iso)
    method: str = ""
    scheme: str = ""
    host: str = ""
    port: int = 0
    path: str = ""
    query: str = ""
    upstream_ip: str = ""

    status_code: int = 0
    latency_ms: int = 0
    first_byte_latency_ms: Optional[int] = None

    stream_duration_ms: Optional[int] = None
    chunk_count: Optional[int] = None
    total_bytes_request: Optional[int] = None
    total_bytes_response: Optional[int] = None
    truncated: bool = False
    tls_decrypted: bool = False

    request_headers: Optional[dict] = None
    request_body: Optional[str] = None
    response_headers: Optional[dict] = None
    response_body: Optional[str] = None

    audit_mode: str = AuditMode.METADATA_ONLY.value
    policy_action: str = "allow"
    error_code: Optional[str] = None

    @classmethod
    def from_identity(cls, identity: SandboxIdentity, **kwargs) -> "AuditRecord":
        return cls(
            sandbox_id=identity.sandbox_id,
            user_id=identity.user_id,
            experiment_id=identity.experiment_id,
            namespace=identity.namespace,
            identity_source=identity.identity_source,
            identity_verified=identity.identity_verified,
            **kwargs,
        )

    def to_dict(self) -> dict:
        d = {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "sandbox_id": self.sandbox_id,
            "user_id": self.user_id,
            "experiment_id": self.experiment_id,
            "namespace": self.namespace,
            "identity_source": self.identity_source,
            "identity_verified": self.identity_verified,
            "timestamp": self.timestamp,
            "method": self.method,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "query": self.query,
            "upstream_ip": self.upstream_ip,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "first_byte_latency_ms": self.first_byte_latency_ms,
            "stream_duration_ms": self.stream_duration_ms,
            "chunk_count": self.chunk_count,
            "total_bytes_request": self.total_bytes_request,
            "total_bytes_response": self.total_bytes_response,
            "truncated": self.truncated,
            "tls_decrypted": self.tls_decrypted,
            "audit_mode": self.audit_mode,
            "policy_action": self.policy_action,
            "error_code": self.error_code,
        }

        if self.audit_mode == AuditMode.FULL_CAPTURE.value:
            d["request_headers"] = self.request_headers
            d["request_body"] = self.request_body
            d["response_headers"] = self.response_headers
            d["response_body"] = self.response_body

        return d


@dataclass
class EgressErrorResponse:
    error_code: str
    message: str
    retryable: bool
    trace_id: str = ""
    sandbox_id: str = ""
    request_id: str = field(default_factory=_new_request_id)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "sandbox_id": self.sandbox_id,
            "request_id": self.request_id,
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }
