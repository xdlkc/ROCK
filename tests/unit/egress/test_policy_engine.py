"""
Cycle 3 — RED: PolicyEngine 策略优先级和灰度匹配测试。
"""

import pytest

from rock.config import (
    EgressAccessPolicy,
    EgressGatewayConfig,
    EgressModeConfig,
    EgressPolicyRule,
)
from rock.egress.policy_engine import PolicyEngine


def make_engine(rules: list[dict], default: str = "metadata-only", policy: dict | None = None) -> PolicyEngine:
    mode = EgressModeConfig(default=default, rules=rules)
    policy_cfg = EgressAccessPolicy(**(policy or {}))
    cfg = EgressGatewayConfig(mode=mode, policy=policy_cfg)
    return PolicyEngine(cfg)


class TestPolicyEngineModeResolution:
    def test_global_default_when_no_rules(self):
        engine = make_engine(rules=[], default="metadata-only")
        mode = engine.resolve_mode("sbx-001", "u-001", "exp-001", "ns-prod", "https://api.openai.com/v1/chat")
        assert mode == "metadata-only"

    def test_global_default_off(self):
        engine = make_engine(rules=[], default="off")
        mode = engine.resolve_mode("sbx-001", "", "", "", "https://any.host.com")
        assert mode == "off"

    # ── Priority 1: sandbox 级 ──────────────────────────────────────

    def test_sandbox_rule_matches(self):
        engine = make_engine(rules=[{"sandbox_id": "sbx-001", "mode": "full-capture"}])
        mode = engine.resolve_mode("sbx-001", "", "", "", "https://any.host.com")
        assert mode == "full-capture"

    def test_sandbox_rule_no_match_falls_through(self):
        engine = make_engine(
            rules=[{"sandbox_id": "sbx-999", "mode": "full-capture"}],
            default="metadata-only",
        )
        mode = engine.resolve_mode("sbx-001", "", "", "", "https://any.host.com")
        assert mode == "metadata-only"

    def test_sandbox_rule_one_vote_veto_overrides_user_rule(self):
        """sandbox 级命中后，同一 sandbox 的 user/route 级规则被忽略。"""
        engine = make_engine(
            rules=[
                {"sandbox_id": "sbx-001", "mode": "off"},
                {"user_id": "u-001", "mode": "full-capture"},
            ]
        )
        mode = engine.resolve_mode("sbx-001", "u-001", "", "", "https://any.host.com")
        assert mode == "off"

    def test_sandbox_rule_one_vote_veto_overrides_route_rule(self):
        engine = make_engine(
            rules=[
                {"sandbox_id": "sbx-001", "mode": "off"},
                {"route_prefix": "https://api.openai.com", "mode": "full-capture"},
            ]
        )
        mode = engine.resolve_mode("sbx-001", "", "", "", "https://api.openai.com/v1/chat")
        assert mode == "off"

    # ── Priority 2: user/experiment/namespace 级 ──────────────────────

    def test_user_id_rule_matches(self):
        engine = make_engine(rules=[{"user_id": "highrisk", "mode": "full-capture"}])
        mode = engine.resolve_mode("sbx-002", "highrisk", "", "", "https://any.host.com")
        assert mode == "full-capture"

    def test_experiment_id_rule_matches(self):
        engine = make_engine(rules=[{"experiment_id": "exp-critical", "mode": "full-capture"}])
        mode = engine.resolve_mode("sbx-002", "", "exp-critical", "", "https://any.host.com")
        assert mode == "full-capture"

    def test_namespace_rule_matches(self):
        engine = make_engine(rules=[{"namespace": "ns-prod", "mode": "full-capture"}])
        mode = engine.resolve_mode("sbx-002", "", "", "ns-prod", "https://any.host.com")
        assert mode == "full-capture"

    def test_multi_dimension_rule_all_must_match(self):
        """规则中同时指定 user_id 和 experiment_id，只有两者都匹配才命中。"""
        engine = make_engine(
            rules=[{"user_id": "u-001", "experiment_id": "exp-001", "mode": "full-capture"}],
            default="metadata-only",
        )
        # 两者都匹配
        assert engine.resolve_mode("sbx", "u-001", "exp-001", "", "") == "full-capture"
        # 只有 user_id 匹配，experiment_id 不匹配 → 不命中
        assert engine.resolve_mode("sbx", "u-001", "exp-999", "", "") == "metadata-only"
        # 只有 experiment_id 匹配 → 不命中
        assert engine.resolve_mode("sbx", "u-999", "exp-001", "", "") == "metadata-only"

    def test_missing_dimension_in_request_treated_as_wildcard(self):
        """规则只指定 user_id，请求中 experiment_id 为空，只要 user_id 匹配即命中。"""
        engine = make_engine(rules=[{"user_id": "u-001", "mode": "full-capture"}])
        mode = engine.resolve_mode("sbx", "u-001", "", "", "")
        assert mode == "full-capture"

    def test_identity_rules_first_match_wins(self):
        """同一层级内多条规则，先声明先生效。"""
        engine = make_engine(
            rules=[
                {"user_id": "u-001", "mode": "full-capture"},
                {"user_id": "u-001", "mode": "off"},  # 第二条永远不会命中
            ]
        )
        mode = engine.resolve_mode("sbx", "u-001", "", "", "")
        assert mode == "full-capture"

    # ── Priority 3: route 级 ──────────────────────────────────────────

    def test_route_prefix_matches(self):
        engine = make_engine(
            rules=[{"route_prefix": "https://api.openai.com", "mode": "metadata-only"}],
            default="off",
        )
        mode = engine.resolve_mode("sbx", "", "", "", "https://api.openai.com/v1/chat/completions")
        assert mode == "metadata-only"

    def test_route_prefix_no_match(self):
        engine = make_engine(
            rules=[{"route_prefix": "https://api.openai.com", "mode": "full-capture"}],
            default="off",
        )
        mode = engine.resolve_mode("sbx", "", "", "", "https://other.api.com/v1")
        assert mode == "off"

    def test_identity_rule_has_higher_priority_than_route(self):
        engine = make_engine(
            rules=[
                {"user_id": "u-001", "mode": "full-capture"},
                {"route_prefix": "https://api.openai.com", "mode": "off"},
            ]
        )
        mode = engine.resolve_mode("sbx", "u-001", "", "", "https://api.openai.com/v1/chat")
        assert mode == "full-capture"

    # ── 综合场景 ────────────────────────────────────────────────────

    def test_priority_order_sandbox_gt_user_gt_route_gt_default(self):
        engine = make_engine(
            rules=[
                {"sandbox_id": "sbx-vip", "mode": "off"},
                {"user_id": "u-vip", "mode": "full-capture"},
                {"route_prefix": "https://internal.api", "mode": "metadata-only"},
            ],
            default="off",
        )
        # sandbox 命中 → off
        assert engine.resolve_mode("sbx-vip", "u-vip", "", "", "https://internal.api/v1") == "off"
        # no sandbox match, user match → full-capture
        assert engine.resolve_mode("sbx-other", "u-vip", "", "", "https://internal.api/v1") == "full-capture"
        # no sandbox, no user match, route match → metadata-only
        assert engine.resolve_mode("sbx-other", "u-other", "", "", "https://internal.api/v1") == "metadata-only"
        # nothing matches → off (default)
        assert engine.resolve_mode("sbx-other", "u-other", "", "", "https://external.api") == "off"


class TestPolicyEngineAccessControl:
    def test_default_allow(self):
        engine = make_engine(rules=[], policy={"default_action": "allow"})
        assert engine.check_access("api.openai.com", 443) == "allow"

    def test_default_deny(self):
        engine = make_engine(rules=[], policy={"default_action": "deny"})
        assert engine.check_access("api.openai.com", 443) == "deny"

    def test_host_in_allow_list(self):
        engine = make_engine(rules=[], policy={
            "default_action": "deny",
            "allow_hosts": ["api.openai.com:443"],
        })
        assert engine.check_access("api.openai.com", 443) == "allow"

    def test_host_not_in_allow_list_with_deny_default(self):
        engine = make_engine(rules=[], policy={
            "default_action": "deny",
            "allow_hosts": ["api.openai.com:443"],
        })
        assert engine.check_access("other.api.com", 443) == "deny"

    def test_host_in_deny_list_overrides_allow_list(self):
        engine = make_engine(rules=[], policy={
            "default_action": "allow",
            "allow_hosts": ["api.openai.com:443"],
            "deny_hosts": ["api.openai.com:443"],
        })
        assert engine.check_access("api.openai.com", 443) == "deny"

    def test_allow_list_empty_falls_back_to_default_action(self):
        engine = make_engine(rules=[], policy={"default_action": "allow", "allow_hosts": []})
        assert engine.check_access("any.host.com", 80) == "allow"
