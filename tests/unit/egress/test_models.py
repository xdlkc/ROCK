"""
Cycle 2 — RED: 数据模型测试。
"""

import pytest

from rock.egress.models import (
    AuditMode,
    AuditRecord,
    EgressErrorCode,
    EgressErrorResponse,
    IdentitySource,
    SandboxIdentity,
)


class TestEgressErrorCode:
    def test_values(self):
        assert EgressErrorCode.POLICY_DENIED == "EGRESS_POLICY_DENIED"
        assert EgressErrorCode.UPSTREAM_TIMEOUT == "EGRESS_UPSTREAM_TIMEOUT"
        assert EgressErrorCode.UPSTREAM_UNREACHABLE == "EGRESS_UPSTREAM_UNREACHABLE"
        assert EgressErrorCode.INTERNAL_ERROR == "EGRESS_GATEWAY_INTERNAL_ERROR"

    def test_http_status_mapping(self):
        assert EgressErrorCode.POLICY_DENIED.to_http_status() == 403
        assert EgressErrorCode.UPSTREAM_TIMEOUT.to_http_status() == 504
        assert EgressErrorCode.UPSTREAM_UNREACHABLE.to_http_status() == 502
        assert EgressErrorCode.INTERNAL_ERROR.to_http_status() == 500

    def test_retryable_hint(self):
        assert EgressErrorCode.POLICY_DENIED.is_retryable() is False
        assert EgressErrorCode.UPSTREAM_TIMEOUT.is_retryable() is True
        assert EgressErrorCode.UPSTREAM_UNREACHABLE.is_retryable() is True
        assert EgressErrorCode.INTERNAL_ERROR.is_retryable() is False


class TestAuditMode:
    def test_values(self):
        assert AuditMode.OFF == "off"
        assert AuditMode.METADATA_ONLY == "metadata-only"
        assert AuditMode.FULL_CAPTURE == "full-capture"


class TestIdentitySource:
    def test_values(self):
        assert IdentitySource.NETWORK_MAPPING == "network_mapping"
        assert IdentitySource.HEADER == "header"
        assert IdentitySource.MIXED == "mixed"


class TestSandboxIdentity:
    def test_default_values(self):
        identity = SandboxIdentity(sandbox_id="sbx-001")
        assert identity.sandbox_id == "sbx-001"
        assert identity.user_id == ""
        assert identity.experiment_id == ""
        assert identity.namespace == ""
        assert identity.identity_source == IdentitySource.NETWORK_MAPPING.value
        assert identity.identity_verified is True

    def test_unverified_identity(self):
        identity = SandboxIdentity(
            sandbox_id="",
            identity_verified=False,
            identity_source=IdentitySource.HEADER.value,
        )
        assert identity.identity_verified is False
        assert identity.identity_source == "header"

    def test_full_identity(self):
        identity = SandboxIdentity(
            sandbox_id="sbx-001",
            user_id="u-001",
            experiment_id="exp-001",
            namespace="ns-prod",
        )
        assert identity.user_id == "u-001"
        assert identity.experiment_id == "exp-001"
        assert identity.namespace == "ns-prod"


class TestAuditRecord:
    def test_auto_request_id(self):
        r1 = AuditRecord()
        r2 = AuditRecord()
        assert r1.request_id != r2.request_id
        assert len(r1.request_id) == 36  # UUID format

    def test_default_audit_mode(self):
        record = AuditRecord()
        assert record.audit_mode == AuditMode.METADATA_ONLY.value

    def test_default_policy_action(self):
        record = AuditRecord()
        assert record.policy_action == "allow"

    def test_streaming_fields_default_none(self):
        record = AuditRecord()
        assert record.stream_duration_ms is None
        assert record.chunk_count is None
        assert record.total_bytes_request is None
        assert record.total_bytes_response is None
        assert record.first_byte_latency_ms is None

    def test_not_truncated_by_default(self):
        record = AuditRecord()
        assert record.truncated is False

    def test_full_capture_fields_default_none(self):
        record = AuditRecord()
        assert record.request_headers is None
        assert record.request_body is None
        assert record.response_headers is None
        assert record.response_body is None

    def test_from_identity(self):
        identity = SandboxIdentity(
            sandbox_id="sbx-001",
            user_id="u-001",
            experiment_id="exp-001",
            namespace="ns-prod",
        )
        record = AuditRecord.from_identity(identity)
        assert record.sandbox_id == "sbx-001"
        assert record.user_id == "u-001"
        assert record.experiment_id == "exp-001"
        assert record.namespace == "ns-prod"
        assert record.identity_source == identity.identity_source
        assert record.identity_verified == identity.identity_verified

    def test_to_dict_metadata_only(self):
        record = AuditRecord(
            sandbox_id="sbx-001",
            method="GET",
            host="api.openai.com",
            port=443,
            status_code=200,
            latency_ms=50,
            audit_mode=AuditMode.METADATA_ONLY.value,
        )
        d = record.to_dict()
        assert d["sandbox_id"] == "sbx-001"
        assert d["method"] == "GET"
        assert d["status_code"] == 200
        # metadata-only 时不应包含 body 内容
        assert d.get("request_body") is None
        assert d.get("response_body") is None

    def test_to_dict_full_capture_includes_body(self):
        record = AuditRecord(
            sandbox_id="sbx-001",
            audit_mode=AuditMode.FULL_CAPTURE.value,
            request_body='{"model": "gpt-4"}',
            response_body='{"id": "chatcmpl-xxx"}',
        )
        d = record.to_dict()
        assert d["request_body"] == '{"model": "gpt-4"}'
        assert d["response_body"] == '{"id": "chatcmpl-xxx"}'


class TestEgressErrorResponse:
    def test_to_dict(self):
        resp = EgressErrorResponse(
            error_code=EgressErrorCode.POLICY_DENIED.value,
            message="Access denied",
            retryable=False,
            trace_id="trace-001",
            sandbox_id="sbx-001",
        )
        d = resp.to_dict()
        assert d["error_code"] == "EGRESS_POLICY_DENIED"
        assert d["message"] == "Access denied"
        assert d["retryable"] is False
        assert d["trace_id"] == "trace-001"
        assert d["sandbox_id"] == "sbx-001"
        assert "request_id" in d
