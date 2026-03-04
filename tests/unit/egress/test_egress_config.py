"""
Cycle 1 — RED: EgressGatewayConfig 配置类解析测试。
运行: uv run pytest tests/unit/egress/test_egress_config.py -v
"""

import pytest

from rock.config import (
    EgressAccessPolicy,
    EgressCaptureConfig,
    EgressGatewayConfig,
    EgressModeConfig,
    EgressPolicyRule,
    EgressRetentionConfig,
    EgressTLSConfig,
    RockConfig,
)


class TestEgressPolicyRule:
    def test_default_values(self):
        rule = EgressPolicyRule()
        assert rule.sandbox_id == ""
        assert rule.user_id == ""
        assert rule.experiment_id == ""
        assert rule.namespace == ""
        assert rule.route_prefix == ""
        assert rule.mode == "metadata-only"

    def test_custom_values(self):
        rule = EgressPolicyRule(sandbox_id="sbx-001", mode="full-capture")
        assert rule.sandbox_id == "sbx-001"
        assert rule.mode == "full-capture"


class TestEgressModeConfig:
    def test_default(self):
        cfg = EgressModeConfig()
        assert cfg.default == "metadata-only"
        assert cfg.rules == []

    def test_parse_rules_from_dicts(self):
        cfg = EgressModeConfig(
            default="off",
            rules=[
                {"sandbox_id": "sbx-001", "mode": "full-capture"},
                {"user_id": "u-001", "mode": "metadata-only"},
            ],
        )
        assert cfg.default == "off"
        assert len(cfg.rules) == 2
        assert isinstance(cfg.rules[0], EgressPolicyRule)
        assert cfg.rules[0].sandbox_id == "sbx-001"
        assert cfg.rules[0].mode == "full-capture"
        assert cfg.rules[1].user_id == "u-001"


class TestEgressCaptureConfig:
    def test_default_redact_fields(self):
        cfg = EgressCaptureConfig()
        assert "authorization" in cfg.redact_fields
        assert "x-api-key" in cfg.redact_fields
        assert "token" in cfg.redact_fields
        assert "cookie" in cfg.redact_fields
        assert "set-cookie" in cfg.redact_fields

    def test_custom_max_body_bytes(self):
        cfg = EgressCaptureConfig(max_body_bytes=1024)
        assert cfg.max_body_bytes == 1024

    def test_default_max_body_bytes(self):
        cfg = EgressCaptureConfig()
        assert cfg.max_body_bytes == 65536

    def test_field_policy_override(self):
        cfg = EgressCaptureConfig(redact_field_policy={"cookie": "drop", "x-api-key": "encrypt"})
        assert cfg.redact_field_policy["cookie"] == "drop"
        assert cfg.redact_field_policy["x-api-key"] == "encrypt"


class TestEgressAccessPolicy:
    def test_default(self):
        policy = EgressAccessPolicy()
        assert policy.default_action == "allow"
        assert policy.allow_hosts == []
        assert policy.deny_hosts == []

    def test_allow_hosts(self):
        policy = EgressAccessPolicy(allow_hosts=["api.openai.com:443"])
        assert "api.openai.com:443" in policy.allow_hosts


class TestEgressGatewayConfig:
    def test_default_disabled(self):
        cfg = EgressGatewayConfig()
        assert cfg.enabled is False

    def test_default_listen_port(self):
        cfg = EgressGatewayConfig()
        assert cfg.listen_port == 18080

    def test_nested_config_from_dicts(self):
        """YAML 加载后是 dict，__post_init__ 应转换为嵌套 dataclass。"""
        cfg = EgressGatewayConfig(
            enabled=True,
            mode={"default": "full-capture", "rules": []},
            policy={"default_action": "deny", "allow_hosts": ["api.openai.com:443"]},
            capture={"max_body_bytes": 32768},
            tls={"enabled": False},
            retention={"metadata_days": 7, "payload_days": 1},
        )
        assert cfg.enabled is True
        assert isinstance(cfg.mode, EgressModeConfig)
        assert cfg.mode.default == "full-capture"
        assert isinstance(cfg.policy, EgressAccessPolicy)
        assert cfg.policy.default_action == "deny"
        assert isinstance(cfg.capture, EgressCaptureConfig)
        assert cfg.capture.max_body_bytes == 32768
        assert isinstance(cfg.tls, EgressTLSConfig)
        assert cfg.tls.enabled is False
        assert isinstance(cfg.retention, EgressRetentionConfig)
        assert cfg.retention.metadata_days == 7

    def test_mode_rules_from_dicts(self):
        """嵌套 rules 列表应被转换为 EgressPolicyRule 对象。"""
        cfg = EgressGatewayConfig(
            mode={
                "default": "metadata-only",
                "rules": [
                    {"sandbox_id": "sbx-canary", "mode": "full-capture"},
                    {"user_id": "highrisk", "experiment_id": "exp-001", "mode": "full-capture"},
                    {"route_prefix": "https://api.openai.com", "mode": "metadata-only"},
                ],
            }
        )
        assert len(cfg.mode.rules) == 3
        assert cfg.mode.rules[0].sandbox_id == "sbx-canary"
        assert cfg.mode.rules[1].user_id == "highrisk"
        assert cfg.mode.rules[2].route_prefix == "https://api.openai.com"

    def test_tls_paths(self):
        cfg = EgressGatewayConfig()
        assert cfg.tls.ca_cert_path == "/etc/rock/egress/ca.crt"
        assert cfg.tls.ca_key_path == "/etc/rock/egress/ca.key"

    def test_streaming_constraints(self):
        cfg = EgressGatewayConfig()
        assert cfg.max_streaming_connections == 1000
        assert cfg.max_connection_duration_seconds == 7200
        assert cfg.idle_timeout_seconds == 300


class TestRockConfigWithEgressGateway:
    def test_rock_config_has_egress_gateway(self):
        config = RockConfig()
        assert hasattr(config, "egress_gateway")
        assert isinstance(config.egress_gateway, EgressGatewayConfig)
        assert config.egress_gateway.enabled is False

    def test_from_dict_with_egress_gateway(self):
        """模拟 from_env() 解析含 egress_gateway 块的 YAML。"""
        import yaml
        import tempfile
        import os

        yaml_content = """
egress_gateway:
  enabled: true
  listen_port: 19090
  mode:
    default: metadata-only
    rules:
      - sandbox_id: "sbx-test"
        mode: full-capture
  policy:
    default_action: allow
    allow_hosts:
      - "api.openai.com:443"
  capture:
    max_body_bytes: 8192
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            tmppath = f.name
        try:
            config = RockConfig.from_env(config_path=tmppath)
            eg = config.egress_gateway
            assert eg.enabled is True
            assert eg.listen_port == 19090
            assert eg.mode.default == "metadata-only"
            assert len(eg.mode.rules) == 1
            assert eg.mode.rules[0].sandbox_id == "sbx-test"
            assert eg.policy.default_action == "allow"
            assert "api.openai.com:443" in eg.policy.allow_hosts
            assert eg.capture.max_body_bytes == 8192
        finally:
            os.unlink(tmppath)
