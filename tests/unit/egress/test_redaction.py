"""
Cycle 4 — RED: RedactionEngine 脱敏规则测试。
"""

import json

import pytest

from rock.egress.redaction import RedactionEngine


class TestMaskStrategy:
    def setup_method(self):
        self.engine = RedactionEngine()

    def test_mask_long_token(self):
        headers = {"Authorization": "Bearer sk-1234567890abcdef"}
        result = self.engine.redact_headers(headers)
        masked = result["Authorization"]
        assert masked.startswith("Bear")
        assert masked.endswith("cdef")
        assert "****" in masked
        # 原始 token 不可见
        assert "sk-1234567890abcdef" not in masked

    def test_mask_short_token_replaced_entirely(self):
        """短 token（≤8 字符）整体替换为 ****。"""
        headers = {"Authorization": "short"}
        result = self.engine.redact_headers(headers)
        assert result["Authorization"] == "****"

    def test_mask_exactly_8_chars(self):
        headers = {"Authorization": "12345678"}
        result = self.engine.redact_headers(headers)
        assert result["Authorization"] == "****"

    def test_mask_9_chars_shows_prefix_suffix(self):
        headers = {"Authorization": "123456789"}
        result = self.engine.redact_headers(headers)
        # len > 8: first4 + **** + last4
        assert result["Authorization"] == "1234****6789"

    def test_non_sensitive_headers_pass_through(self):
        headers = {"Content-Type": "application/json", "X-Request-ID": "req-001"}
        result = self.engine.redact_headers(headers)
        assert result["Content-Type"] == "application/json"
        assert result["X-Request-ID"] == "req-001"

    def test_case_insensitive_field_matching(self):
        headers = {"AUTHORIZATION": "Bearer secret-token-12345"}
        result = self.engine.redact_headers(headers)
        assert "secret-token-12345" not in result.get("AUTHORIZATION", "")

    def test_x_api_key_masked_by_default(self):
        headers = {"x-api-key": "sk-abcdef1234567890"}
        result = self.engine.redact_headers(headers)
        assert "abcdef1234567890" not in result.get("x-api-key", "")

    def test_cookie_masked_by_default(self):
        headers = {"cookie": "session=abcdef1234567890xyz"}
        result = self.engine.redact_headers(headers)
        assert "abcdef1234567890xyz" not in result.get("cookie", "")


class TestDropStrategy:
    def test_drop_removes_field_from_headers(self):
        engine = RedactionEngine(redact_field_policy={"cookie": "drop"})
        headers = {"cookie": "session=abc123", "Content-Type": "application/json"}
        result = engine.redact_headers(headers)
        assert "cookie" not in result
        assert result["Content-Type"] == "application/json"

    def test_drop_from_json_body(self):
        engine = RedactionEngine(redact_field_policy={"token": "drop"})
        body = json.dumps({"token": "secret-tok-12345", "model": "gpt-4"})
        result = engine.redact_json_body(body)
        data = json.loads(result)
        assert "token" not in data
        assert data["model"] == "gpt-4"


class TestEncryptStrategy:
    def test_encrypt_strategy_with_key(self):
        from rock.utils.crypto_utils import AESEncryption

        key = AESEncryption.generate_key()
        engine = RedactionEngine(
            redact_field_policy={"authorization": "encrypt"},
            aes_key=key,
        )
        headers = {"Authorization": "Bearer sk-1234567890abcdef"}
        result = engine.redact_headers(headers)
        encrypted_val = result["Authorization"]
        # 加密后应有前缀标记
        assert encrypted_val.startswith("enc:")
        # 原始值不可见
        assert "sk-1234567890abcdef" not in encrypted_val

    def test_encrypt_without_key_falls_back_to_mask(self):
        """未配置 AES key 时，encrypt 策略降级为 mask。"""
        engine = RedactionEngine(
            redact_field_policy={"authorization": "encrypt"},
            aes_key=None,
        )
        headers = {"Authorization": "Bearer sk-1234567890abcdef"}
        result = engine.redact_headers(headers)
        assert "****" in result["Authorization"]
        assert "sk-1234567890abcdef" not in result["Authorization"]


class TestJsonBodyRedaction:
    def setup_method(self):
        self.engine = RedactionEngine()

    def test_redact_top_level_key(self):
        body = json.dumps({"apiKey": "my-secret-key-12345", "model": "gpt-4"})
        result = self.engine.redact_json_body(body)
        data = json.loads(result)
        assert "my-secret-key-12345" not in data.get("apiKey", "")
        assert data["model"] == "gpt-4"

    def test_non_json_body_returned_unchanged(self):
        body = "not-json-content"
        result = self.engine.redact_json_body(body)
        assert result == "not-json-content"

    def test_empty_body_returned_unchanged(self):
        result = self.engine.redact_json_body("")
        assert result == ""

    def test_nested_dict_redaction(self):
        body = json.dumps({"auth": {"token": "secret-tok-12345"}, "data": "value"})
        result = self.engine.redact_json_body(body)
        data = json.loads(result)
        assert "secret-tok-12345" not in data["auth"].get("token", "")

    def test_multiple_sensitive_fields(self):
        body = json.dumps({
            "authorization": "Bearer sk-123456789012",
            "x-api-key": "key-abcdef123456",
            "content": "hello",
        })
        result = self.engine.redact_json_body(body)
        data = json.loads(result)
        assert "sk-123456789012" not in data.get("authorization", "")
        assert "key-abcdef123456" not in data.get("x-api-key", "")
        assert data["content"] == "hello"


class TestCustomRedactFields:
    def test_custom_fields_list(self):
        engine = RedactionEngine(redact_fields=["my-secret-header"])
        headers = {"my-secret-header": "sensitive-value-12345", "safe-header": "safe-value"}
        result = engine.redact_headers(headers)
        assert "sensitive-value-12345" not in result.get("my-secret-header", "")
        assert result["safe-header"] == "safe-value"

    def test_default_fields_not_redacted_if_custom_list_provided(self):
        """提供自定义列表后，默认字段（如 authorization）不再自动脱敏。"""
        engine = RedactionEngine(redact_fields=["my-custom-field"])
        headers = {"Authorization": "Bearer secret-12345"}
        result = engine.redact_headers(headers)
        # 没有在自定义列表中，不脱敏
        assert result["Authorization"] == "Bearer secret-12345"
