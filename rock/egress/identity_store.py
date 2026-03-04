import json
import time
from typing import Optional

from rock.egress.models import SandboxIdentity
from rock.logger import init_logger

logger = init_logger(__name__)

REDIS_KEY_PREFIX = "egress:identity:"


class _CacheEntry:
    __slots__ = ("identity", "expires_at")

    def __init__(self, identity: Optional[SandboxIdentity], expires_at: float) -> None:
        self.identity = identity
        self.expires_at = expires_at


class IdentityStore:
    """
    维护 container_ip → SandboxIdentity 的双层映射：
      1. Redis（持久层，跨进程共享）
      2. 进程内 TTL 字典缓存（减少 Redis 查询）

    写入方：DockerDeployment.start() 在容器启动后调用 write_identity()。
    清理方：DockerDeployment.stop() 调用 delete_identity()。
    """

    def __init__(self, redis_client, cache_ttl_seconds: int = 30) -> None:
        self._redis = redis_client
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, _CacheEntry] = {}

    async def write_identity(self, container_ip: str, identity: SandboxIdentity, ttl_seconds: int = 3600) -> None:
        key = self._redis_key(container_ip)
        payload = json.dumps({
            "sandbox_id": identity.sandbox_id,
            "user_id": identity.user_id,
            "experiment_id": identity.experiment_id,
            "namespace": identity.namespace,
            "identity_source": identity.identity_source,
            "identity_verified": identity.identity_verified,
        })
        await self._redis.set(key, payload, ex=ttl_seconds)
        self._set_cache(container_ip, identity)

    async def get_identity(self, container_ip: str) -> Optional[SandboxIdentity]:
        # 优先命中内存缓存
        cached = self._get_cache(container_ip)
        if cached is not None:
            return cached

        key = self._redis_key(container_ip)
        raw = await self._redis.get(key)
        if raw is None:
            return None

        try:
            data = json.loads(raw)
            identity = SandboxIdentity(
                sandbox_id=data.get("sandbox_id", ""),
                user_id=data.get("user_id", ""),
                experiment_id=data.get("experiment_id", ""),
                namespace=data.get("namespace", ""),
                identity_source=data.get("identity_source", "network_mapping"),
                identity_verified=data.get("identity_verified", True),
            )
            self._set_cache(container_ip, identity)
            return identity
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"IdentityStore: failed to parse identity for {container_ip}: {e}")
            return None

    async def delete_identity(self, container_ip: str) -> None:
        key = self._redis_key(container_ip)
        await self._redis.delete(key)
        self._invalidate_cache(container_ip)

    @staticmethod
    def _redis_key(container_ip: str) -> str:
        return f"{REDIS_KEY_PREFIX}{container_ip}"

    def _get_cache(self, ip: str) -> Optional[SandboxIdentity]:
        entry = self._cache.get(ip)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._cache[ip]
            return None
        return entry.identity

    def _set_cache(self, ip: str, identity: SandboxIdentity) -> None:
        self._cache[ip] = _CacheEntry(identity, time.monotonic() + self._cache_ttl)

    def _invalidate_cache(self, ip: str) -> None:
        self._cache.pop(ip, None)
