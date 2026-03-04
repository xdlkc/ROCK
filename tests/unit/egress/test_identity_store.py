"""
Cycle 5 — RED: IdentityStore Redis 读写测试（使用 FakeRedis）。
"""

import asyncio

import pytest
from fakeredis import aioredis

from rock.egress.identity_store import IdentityStore
from rock.egress.models import IdentitySource, SandboxIdentity


@pytest.fixture
async def fake_redis():
    client = aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def store(fake_redis):
    s = IdentityStore(redis_client=fake_redis, cache_ttl_seconds=5)
    yield s


class TestIdentityStoreWrite:
    async def test_write_and_read_identity(self, store):
        identity = SandboxIdentity(
            sandbox_id="sbx-001",
            user_id="u-001",
            experiment_id="exp-001",
            namespace="ns-prod",
        )
        await store.write_identity("192.168.1.10", identity, ttl_seconds=300)

        result = await store.get_identity("192.168.1.10")
        assert result is not None
        assert result.sandbox_id == "sbx-001"
        assert result.user_id == "u-001"
        assert result.experiment_id == "exp-001"
        assert result.namespace == "ns-prod"
        assert result.identity_verified is True

    async def test_get_unknown_ip_returns_none(self, store):
        result = await store.get_identity("10.0.0.99")
        assert result is None

    async def test_delete_identity(self, store):
        identity = SandboxIdentity(sandbox_id="sbx-002")
        await store.write_identity("192.168.1.11", identity, ttl_seconds=300)

        await store.delete_identity("192.168.1.11")
        result = await store.get_identity("192.168.1.11")
        assert result is None

    async def test_overwrite_identity(self, store):
        identity1 = SandboxIdentity(sandbox_id="sbx-old")
        await store.write_identity("192.168.1.12", identity1, ttl_seconds=300)

        identity2 = SandboxIdentity(sandbox_id="sbx-new", user_id="u-999")
        await store.write_identity("192.168.1.12", identity2, ttl_seconds=300)

        result = await store.get_identity("192.168.1.12")
        assert result.sandbox_id == "sbx-new"
        assert result.user_id == "u-999"


class TestIdentityStoreCache:
    async def test_cache_hit_avoids_redis(self, fake_redis):
        """写入后第二次读取应命中内存缓存（Redis key 已删除也能读取）。"""
        store = IdentityStore(redis_client=fake_redis, cache_ttl_seconds=60)
        identity = SandboxIdentity(sandbox_id="sbx-cached")
        await store.write_identity("192.168.1.20", identity, ttl_seconds=300)

        # 第一次读，写入缓存
        result1 = await store.get_identity("192.168.1.20")
        assert result1.sandbox_id == "sbx-cached"

        # 直接从 Redis 删除，模拟"Redis 已删但缓存未过期"
        await fake_redis.delete("egress:identity:192.168.1.20")

        # 第二次读，应仍命中缓存
        result2 = await store.get_identity("192.168.1.20")
        assert result2 is not None
        assert result2.sandbox_id == "sbx-cached"

    async def test_delete_clears_cache(self, fake_redis):
        store = IdentityStore(redis_client=fake_redis, cache_ttl_seconds=60)
        identity = SandboxIdentity(sandbox_id="sbx-del")
        await store.write_identity("192.168.1.21", identity, ttl_seconds=300)

        # 预热缓存
        await store.get_identity("192.168.1.21")

        # 通过 store.delete_identity 同时清理缓存
        await store.delete_identity("192.168.1.21")

        result = await store.get_identity("192.168.1.21")
        assert result is None


class TestIdentityStoreRedisKey:
    async def test_redis_key_format(self, fake_redis):
        store = IdentityStore(redis_client=fake_redis, cache_ttl_seconds=5)
        identity = SandboxIdentity(sandbox_id="sbx-key-test")
        await store.write_identity("10.0.1.100", identity, ttl_seconds=300)

        # 验证 Redis key 格式
        key = await fake_redis.get("egress:identity:10.0.1.100")
        assert key is not None

    async def test_identity_source_preserved(self, store):
        identity = SandboxIdentity(
            sandbox_id="sbx-src",
            identity_source=IdentitySource.NETWORK_MAPPING.value,
            identity_verified=True,
        )
        await store.write_identity("192.168.2.1", identity, ttl_seconds=300)
        result = await store.get_identity("192.168.2.1")
        assert result.identity_source == IdentitySource.NETWORK_MAPPING.value
        assert result.identity_verified is True
