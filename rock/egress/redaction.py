import json
from typing import Optional

from rock.utils.crypto_utils import AESEncryption

STRATEGY_MASK = "mask"
STRATEGY_DROP = "drop"
STRATEGY_ENCRYPT = "encrypt"

_DEFAULT_REDACT_FIELDS = frozenset([
    "authorization", "x-api-key", "apikey", "token", "cookie", "set-cookie"
])


class RedactionEngine:
    """
    对请求/响应头和 JSON body 中的敏感字段进行脱敏处理。

    字段级策略：
      - mask（默认）: 保留前 4 位和后 4 位，中间替换为 ****；≤8 字符整体替换。
      - drop: 从结果中移除该字段。
      - encrypt: 使用 AES-GCM-256 加密，需配置 aes_key；未配置时降级为 mask。
    """

    def __init__(
        self,
        redact_fields: Optional[list[str]] = None,
        redact_field_policy: Optional[dict[str, str]] = None,
        aes_key: Optional[str] = None,
    ) -> None:
        if redact_fields is None:
            self._redact_fields = _DEFAULT_REDACT_FIELDS
        else:
            self._redact_fields = frozenset(f.lower() for f in redact_fields)

        self._field_policy: dict[str, str] = (
            {k.lower(): v for k, v in redact_field_policy.items()} if redact_field_policy else {}
        )
        self._aes: Optional[AESEncryption] = AESEncryption(aes_key) if aes_key else None

    def redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """返回脱敏后的 headers 副本（不修改原始 dict）。"""
        result: dict[str, str] = {}
        for key, value in headers.items():
            lower_key = key.lower()
            if lower_key in self._redact_fields:
                strategy = self._field_policy.get(lower_key, STRATEGY_MASK)
                redacted = self._apply(value, strategy)
                if redacted is not None:
                    result[key] = redacted
            else:
                result[key] = value
        return result

    def redact_json_body(self, body: str) -> str:
        """对 JSON 格式的 body 递归脱敏，非 JSON 内容原样返回。"""
        if not body:
            return body
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                self._redact_dict_inplace(data)
                return json.dumps(data)
        except (json.JSONDecodeError, TypeError):
            pass
        return body

    def _redact_dict_inplace(self, data: dict) -> None:
        for key in list(data.keys()):
            lower_key = key.lower()
            if lower_key in self._redact_fields:
                strategy = self._field_policy.get(lower_key, STRATEGY_MASK)
                if strategy == STRATEGY_DROP:
                    del data[key]
                else:
                    redacted = self._apply(str(data[key]), strategy)
                    if redacted is None:
                        del data[key]
                    else:
                        data[key] = redacted
            elif isinstance(data.get(key), dict):
                self._redact_dict_inplace(data[key])

    def _apply(self, value: str, strategy: str) -> Optional[str]:
        if strategy == STRATEGY_DROP:
            return None
        if strategy == STRATEGY_ENCRYPT:
            if self._aes:
                return "enc:" + self._aes.encrypt(value)
            # 无 key 时降级为 mask
            return self._mask(value)
        return self._mask(value)

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 8:
            return "****"
        return value[:4] + "****" + value[-4:]
