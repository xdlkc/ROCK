# Sandbox Egress Gateway 技术方案

> **基于 spec**：`docs/dev/specs/sandbox-egress-gateway-spec.md`  
> **版本**：v1.0  
> **日期**：2026-03-05

---

## 1. 方案概述

### 1.1 核心思路

在每台宿主机上部署一个独立的 **Egress Gateway 进程**（节点级形态），作为与 `admin` / `rocklet` 并列的第四个 ROCK 服务。沙箱出站的 HTTP/HTTPS 流量通过宿主机 iptables 规则强制重定向到该进程；Gateway 负责识别 sandbox 身份、执行访问控制策略、记录审计日志，然后将流量转发到真实目标。

### 1.2 技术选型：mitmproxy 作为代理引擎

| 方案 | 优点 | 缺点 |
|---|---|---|
| **mitmproxy（库模式）** ✅ | Python 原生；内置 HTTPS MITM；支持 SSE/WebSocket/gRPC/HTTP2；Addon 插件体系 | 单进程性能上限，依赖较重 |
| 自研 asyncio 代理 | 完全可控 | SSE/WebSocket/gRPC MITM 工作量极大；HTTPS MITM 需自建 |
| Envoy/Nginx + 控制面 | 高性能 | 引入非 Python 二进制依赖；运维复杂；与现有栈割裂 |

**决策**：使用 `mitmproxy` 的 Python API（`mitmproxy.options` + `mitmproxy.tools.dump.DumpMaster`）以 Library 模式嵌入，通过 Addon 机制注入身份识别、策略检查、审计采集逻辑。

---

## 2. 模块结构

```
rock/
└── egress/                          # 新增：Egress Gateway 服务
    ├── __init__.py
    ├── server.py                    # 服务入口，启动 mitmproxy DumpMaster
    ├── config.py                    # EgressGatewayConfig（完整配置模型）
    ├── addons/
    │   ├── identity.py              # IdentityAddon：从 Redis 映射 IP→sandbox 身份
    │   ├── policy.py                # PolicyAddon：allow/deny 策略检查
    │   ├── audit.py                 # AuditAddon：审计日志采集（含流式）
    │   └── redaction.py             # RedactionAddon：敏感字段脱敏
    ├── identity_store.py            # Redis 读写：ip:port → SandboxIdentity
    ├── audit_writer.py              # 审计写入：结构化 JSON 落盘 + 异步队列
    ├── policy_engine.py             # 模式匹配与优先级规则
    ├── tls_ca.py                    # 自签 CA 管理（生成/加载）
    └── models.py                    # 内部数据模型（AuditRecord、SandboxIdentity 等）
```

新增集成改动位置：

```
rock/
├── config.py                        # + EgressGatewayConfig 及其子配置
├── env_vars.py                      # + ROCK_EGRESS_GATEWAY_ENABLED
├── deployments/docker.py            # + iptables 规则注入/清理，+ Redis 身份映射写入
├── cli/main.py                      # + `egress` 子命令入口
└── admin/core/redis_key.py          # + EGRESS_IDENTITY_PREFIX 常量
```

---

## 3. 配置设计

### 3.1 新增配置类（`rock/config.py`）

```python
@dataclass
class EgressCaptureConfig:
    max_body_bytes: int = 65536
    max_chunk_capture: int = 0   # 0 = 不限 chunk 数，仅受 max_body_bytes 约束
    redact_fields: list[str] = field(default_factory=lambda: [
        "authorization", "x-api-key", "apiKey", "token", "cookie", "set-cookie"
    ])
    # 字段级策略覆盖：{"x-api-key": "encrypt", "cookie": "drop"}
    redact_field_policy: dict[str, str] = field(default_factory=dict)


@dataclass
class EgressPolicyRule:
    sandbox_id: str = ""
    user_id: str = ""
    experiment_id: str = ""
    namespace: str = ""
    route_prefix: str = ""
    mode: str = "metadata-only"    # off | metadata-only | full-capture


@dataclass
class EgressModeConfig:
    default: str = "metadata-only"  # off | metadata-only | full-capture
    rules: list[EgressPolicyRule] = field(default_factory=list)

    def __post_init__(self):
        if self.rules and isinstance(self.rules[0], dict):
            self.rules = [EgressPolicyRule(**r) for r in self.rules]


@dataclass
class EgressAccessPolicy:
    default_action: str = "allow"   # allow | deny
    allow_hosts: list[str] = field(default_factory=list)   # "host:port"
    deny_hosts: list[str] = field(default_factory=list)


@dataclass
class EgressRetentionConfig:
    metadata_days: int = 30
    payload_days: int = 7


@dataclass
class EgressTLSConfig:
    enabled: bool = True
    ca_cert_path: str = "/etc/rock/egress/ca.crt"
    ca_key_path: str = "/etc/rock/egress/ca.key"


@dataclass
class EgressGatewayConfig:
    enabled: bool = False
    listen_port: int = 18080          # iptables 重定向目标端口
    mode: EgressModeConfig = field(default_factory=EgressModeConfig)
    policy: EgressAccessPolicy = field(default_factory=EgressAccessPolicy)
    capture: EgressCaptureConfig = field(default_factory=EgressCaptureConfig)
    tls: EgressTLSConfig = field(default_factory=EgressTLSConfig)
    retention: EgressRetentionConfig = field(default_factory=EgressRetentionConfig)
    # 审计日志目录（本地 JSON 落盘）
    audit_log_dir: str = "/var/log/rock/egress"
    # 流式连接资源约束
    max_streaming_connections: int = 1000
    max_connection_duration_seconds: int = 7200   # 2小时
    idle_timeout_seconds: int = 300
    # 策略缓存刷新间隔（秒）
    policy_reload_interval_seconds: int = 10

    def __post_init__(self):
        if isinstance(self.mode, dict):
            self.mode = EgressModeConfig(**self.mode)
        if isinstance(self.policy, dict):
            self.policy = EgressAccessPolicy(**self.policy)
        if isinstance(self.capture, dict):
            self.capture = EgressCaptureConfig(**self.capture)
        if isinstance(self.tls, dict):
            self.tls = EgressTLSConfig(**self.tls)
        if isinstance(self.retention, dict):
            self.retention = EgressRetentionConfig(**self.retention)
```

`RockConfig` 中新增字段：
```python
egress_gateway: EgressGatewayConfig = field(default_factory=EgressGatewayConfig)
```

`from_env()` 中补充：
```python
if "egress_gateway" in config:
    kwargs["egress_gateway"] = EgressGatewayConfig(**config["egress_gateway"])
```

### 3.2 环境变量（`rock/env_vars.py`）

```python
ROCK_EGRESS_GATEWAY_ENABLED: bool   # 默认 False；优先级低于配置文件中的 egress_gateway.enabled
```

优先级规则：若 `egress_gateway.enabled` 在 YAML 中显式声明，以 YAML 为准；否则降级读取 `ROCK_EGRESS_GATEWAY_ENABLED`。

### 3.3 YAML 配置示例

```yaml
egress_gateway:
  enabled: true
  listen_port: 18080
  audit_log_dir: /var/log/rock/egress

  tls:
    enabled: true
    ca_cert_path: /etc/rock/egress/ca.crt
    ca_key_path:  /etc/rock/egress/ca.key

  mode:
    default: metadata-only
    rules:
      - match:
          user_id: "highrisk-user"
          experiment_id: "exp-critical"
        mode: full-capture
      - match:
          sandbox_id: "sbx-canary-001"
        mode: full-capture

  policy:
    default_action: allow
    allow_hosts:
      - "api.openai.com:443"
      - "model-runner.internal:12434"

  capture:
    max_body_bytes: 65536
    redact_fields:
      - authorization
      - x-api-key
      - apiKey
      - token
      - cookie

  retention:
    metadata_days: 30
    payload_days: 7
```

---

## 4. 核心组件设计

### 4.1 服务入口（`rock/egress/server.py`）

```
EgressServer
 ├── 加载 EgressGatewayConfig
 ├── 初始化 TLSCAManager（生成或加载自签 CA）
 ├── 初始化 IdentityStore（连接 Redis）
 ├── 初始化 AuditWriter（异步写入 JSON 日志）
 ├── 初始化 PolicyEngine（加载规则，定时热重载）
 ├── 注册 mitmproxy addons：
 │    IdentityAddon → PolicyAddon → RedactionAddon → AuditAddon
 └── 启动 mitmproxy（mode=transparent, listen_port=18080）
```

CLI 入口（`rock/cli/main.py`）：
```
rock egress --port 18080 --config /etc/rock/rock-local.yml
```

`pyproject.toml` 中增加 entry point：
```toml
egress = "rock.egress.server:main"
```

### 4.2 身份识别（`rock/egress/identity_store.py`）

**写入方**：`DockerDeployment.start()` 在容器启动、IP 确定后，向 Redis 写入：

```
Key:   EGRESS_IDENTITY:{container_ip}
Value: {
    "sandbox_id": "sbx-xxx",
    "user_id": "u-xxx",
    "experiment_id": "exp-xxx",
    "namespace": "ns-xxx",
    "created_at": 1234567890
}
TTL:   container 生命周期 + 60s buffer
```

Redis key 前缀常量：`EGRESS_IDENTITY_PREFIX = "egress:identity:"` 定义于 `rock/admin/core/redis_key.py`。

**读取方（`IdentityAddon`）**：
- 从 mitmproxy `client_conn.peername[0]`（客户端 IP，即容器 IP）查询 Redis
- 结果缓存于进程内 `TTLCache`（默认 30s，可配置）
- 命中：填充 `flow.metadata["sandbox_identity"]`，标记 `identity_verified=True`，`identity_source="network_mapping"`
- 未命中：标记 `identity_verified=False`，请求仍放行，审计中记录告警

**清理**：`DockerDeployment.stop()` 删除对应 Redis key，避免容器 IP 复用误识别。

### 4.3 策略引擎（`rock/egress/policy_engine.py`）

**访问控制**（allow/deny）：

```python
def check_access(host: str, port: int, identity: SandboxIdentity) -> PolicyResult:
    # 按 deny_hosts → allow_hosts → default_action 顺序判断
```

**审计模式选取**（优先级从高到低）：

```
1. sandbox 级匹配（sandbox_id 命中）
2. user/experiment/namespace 级匹配（维度按 OR 逻辑匹配，缺失字段视为通配）
3. route 级匹配（route_prefix 前缀匹配）
4. 全局 default
```

同一层级多条规则：**先声明先生效**（遍历 rules 列表，首个命中返回）。

高优先级**一票否决**：sandbox 级一旦命中，忽略该 sandbox 下所有低优先级规则。

**热重载**：每 `policy_reload_interval_seconds` 秒从 Redis（或本地配置文件）重新加载策略，无需重启进程。策略以 `asyncio.Lock` 保护原子替换。

### 4.4 审计采集（`rock/egress/addons/audit.py`）

mitmproxy Addon 生命周期钩子：

| 钩子 | 采集动作 |
|---|---|
| `requestheaders(flow)` | 记录请求元数据，生成 `request_id`（UUID），记录 `timestamp` |
| `request(flow)` | 在 full-capture 模式下缓存 request body（受 max_body_bytes 限制） |
| `responseheaders(flow)` | 记录 `status_code`，计算 `first_byte_latency_ms` |
| `response(flow)` | 在 full-capture 模式下缓存 response body，计算 `latency_ms` |
| `error(flow)` | 映射错误码，记录错误审计记录 |

**流式场景**（SSE/Chunked/WebSocket/gRPC）：

```python
class StreamingAuditAddon:
    def websocket_message(self, flow):
        # 累积 chunk_count、total_bytes；full-capture 下记录 message content
    
    def response_chunk(self, flow, chunk):
        # chunk_count++，total_bytes += len(chunk)
        # 累积内容，超 max_body_bytes 截断并标记 truncated=True
```

**审计记录结构（`AuditRecord`）**：

```python
@dataclass
class AuditRecord:
    # 核心身份
    request_id: str            # UUID，全局唯一
    trace_id: str
    sandbox_id: str
    user_id: str
    experiment_id: str
    namespace: str
    identity_source: str       # network_mapping | header | mixed
    identity_verified: bool

    # 请求元数据
    timestamp: str             # ISO8601
    method: str
    scheme: str
    host: str
    port: int
    path: str
    query: str
    upstream_ip: str

    # 响应元数据
    status_code: int
    latency_ms: int
    first_byte_latency_ms: int | None

    # 流式字段
    stream_duration_ms: int | None
    chunk_count: int | None
    total_bytes_request: int | None
    total_bytes_response: int | None
    truncated: bool = False

    # Full-capture 内容（脱敏后）
    request_headers: dict | None = None
    request_body: str | None = None
    response_headers: dict | None = None
    response_body: str | None = None

    # 审计模式与策略
    audit_mode: str            # off | metadata-only | full-capture
    policy_action: str         # allow | deny
    error_code: str | None = None
```

### 4.5 脱敏引擎（`rock/egress/addons/redaction.py`）

字段级策略：

| 策略 | 行为 |
|---|---|
| `drop` | 从审计记录中移除该字段，不存储 |
| `mask`（默认） | 仅保留前 4 位和后 4 位，中间替换为 `****` |
| `encrypt` | 使用 `rock.utils.crypto_utils.AESCipher` 加密后存储，依赖 `aes_encrypt_key` |

脱敏作用于：请求头、响应头、body 中的 JSON 字段（若 Content-Type 为 application/json）。

### 4.6 TLS MITM（`rock/egress/tls_ca.py`）

```
TLSCAManager
 ├── auto_generate(): 若 ca_cert_path/ca_key_path 不存在，用 cryptography 库生成 4096-bit RSA 自签 CA
 ├── load(): 加载已有 CA cert + key
 └── get_mitmproxy_cert_store(): 返回 mitmproxy 使用的 CertStore 实例
```

CA 信任部署（运维侧）：ROCK 需在 sandbox 镜像构建时将 CA cert 预置到容器系统信任链（`/usr/local/share/ca-certificates/`），并执行 `update-ca-certificates`。

降级模式：当 sandbox 镜像未完成 CA 信任部署时，HTTPS full-capture 仅记录元数据与 TLS 会话信息，审计记录中标记 `tls_decrypted=false`。

### 4.7 审计写入（`rock/egress/audit_writer.py`）

```
AuditWriter
 ├── asyncio.Queue（有界，默认 maxsize=10000）
 ├── 后台 asyncio Task 消费队列，以 JSON Lines 格式写入日志文件
 ├── 日志文件路径：{audit_log_dir}/{date}/audit.jsonl
 ├── 当队列满时：丢弃新记录（不阻塞转发路径），metrics counter 递增
 └── 写入失败时：仅输出指标告警，不阻断请求转发
```

日志轮转：通过系统 `logrotate` 或 Python `logging.handlers.TimedRotatingFileHandler` 管理。

---

## 5. 与沙箱生命周期集成

### 5.1 iptables 规则注入

在 `DockerDeployment.start()` 中，容器启动并获取到容器 IP 后，调用 `EgressNetworkManager.inject()`：

```bash
# 将 sandbox 容器（container_ip）出站 HTTP/HTTPS 流量重定向到 Gateway
iptables -t nat -A PREROUTING \
    -s {container_ip} \
    -p tcp --dport 80 \
    -j REDIRECT --to-port 18080

iptables -t nat -A PREROUTING \
    -s {container_ip} \
    -p tcp --dport 443 \
    -j REDIRECT --to-port 18080

# 防止 Gateway 进程自身的流量被重定向（避免循环）
iptables -t nat -I PREROUTING \
    -m owner --uid-owner {egress_uid} \
    -j RETURN
```

在 `DockerDeployment.stop()` 中，调用 `EgressNetworkManager.cleanup(container_ip)` 删除对应规则。

> **注意**：iptables 操作需要宿主机 root 权限；在 `DockerDeployment` 中通过 `subprocess` 调用，失败时记录告警但不阻断 sandbox 启停。Gateway 未启用时（`enabled=False`）跳过此步骤。

### 5.2 Redis 身份映射时序

```
sandbox start (DockerDeployment.start)
    │
    ├─ docker run → 获取 container_ip
    ├─ redis.set("egress:identity:{container_ip}", {sandbox_id, user_id, ...}, ex=ttl)
    └─ iptables inject(container_ip)

sandbox stop (DockerDeployment.stop)
    │
    ├─ iptables cleanup(container_ip)
    └─ redis.delete("egress:identity:{container_ip}")
```

TTL 设定：`sandbox auto_clear_time_minutes * 60 + 300`（5 分钟 buffer），避免因 stop 失败导致孤儿记录永久占位。

---

## 6. 可观测性（Metrics）

复用现有 OpenTelemetry 接入，新增以下 Prometheus 指标（通过 `rock/admin/metrics/` 或 Gateway 独立暴露）：

| 指标 | 类型 | 说明 |
|---|---|---|
| `egress_requests_total` | Counter | 按 `sandbox_id`, `status_code`, `error_code` 分组 |
| `egress_policy_denied_total` | Counter | 策略拒绝次数 |
| `egress_audit_write_failures_total` | Counter | 审计写入失败次数 |
| `egress_audit_queue_drops_total` | Counter | 队列丢弃次数（"审计降级"告警触发点） |
| `egress_streaming_connections` | Gauge | 当前活跃流式连接数 |
| `egress_request_duration_seconds` | Histogram | P50/P95/P99 延迟（按模式分组）|
| `egress_identity_unverified_total` | Counter | 身份未识别次数 |

告警规则（Alerting）：
- `egress_audit_queue_drops_total > 0` → 触发"审计降级"告警
- `egress_identity_unverified_total` 突增 → 触发安全告警

---

## 7. 错误码与 HTTP 映射

```python
class EgressErrorCode(str, Enum):
    POLICY_DENIED      = "EGRESS_POLICY_DENIED"       # HTTP 403
    UPSTREAM_TIMEOUT   = "EGRESS_UPSTREAM_TIMEOUT"    # HTTP 504
    UPSTREAM_UNREACHABLE = "EGRESS_UPSTREAM_UNREACHABLE"  # HTTP 502
    INTERNAL_ERROR     = "EGRESS_GATEWAY_INTERNAL_ERROR"  # HTTP 500
```

错误响应体（JSON）：
```json
{
    "trace_id": "xxx",
    "sandbox_id": "sbx-xxx",
    "request_id": "uuid",
    "error_code": "EGRESS_POLICY_DENIED",
    "message": "Access to api.example.com:443 denied by policy",
    "retryable": false
}
```

`retryable` 字段仅作客户端建议，Gateway 本身不重试 upstream。

---

## 8. 依赖变更

在 `pyproject.toml` 中新增依赖组 `[project.optional-dependencies] egress`：

```toml
[project.optional-dependencies]
egress = [
    "mitmproxy>=10.0.0",          # 代理引擎，内置 HTTPS MITM + SSE/WS/gRPC 支持
    "cryptography>=39.0.1",       # 已在 admin 依赖中，TLS CA 生成
    "cachetools>=5.0.0",          # TTLCache（身份缓存）
]
```

mitmproxy 已包含 `h2`（HTTP/2/gRPC）和 `wsproto`（WebSocket），无需额外引入。

---

## 9. 分阶段实施计划

### Phase 1：基础框架与 metadata-only（预计 2 周）

**目标**：验证网络收口 + 基础身份识别 + metadata-only 审计可用。

- [ ] `EgressGatewayConfig` 及相关配置类，集成到 `RockConfig`
- [ ] `rock/egress/` 模块骨架（`server.py`, `models.py`, `identity_store.py`）
- [ ] mitmproxy 集成（透明代理模式，HTTP 转发）
- [ ] `IdentityAddon`：Redis 映射查询，`identity_verified` 标记
- [ ] `AuditAddon`：metadata-only 模式下的元数据采集
- [ ] `AuditWriter`：JSON Lines 落盘（本地文件）
- [ ] `DockerDeployment` 集成：iptables 注入/清理，Redis 身份写入/清理
- [ ] `egress` CLI 入口
- [ ] 单元测试：身份映射、metadata-only 行为
- [ ] 集成测试（canary sandbox）：验证流量收口与日志可查

**验收（AC-1、AC-2、AC-6）**

### Phase 2：HTTPS MITM + full-capture + 脱敏（预计 2 周）

**目标**：全链路 full-capture，含 HTTPS 明文捕获和脱敏。

- [ ] `TLSCAManager`：CA 生成/加载，mitmproxy CertStore 集成
- [ ] `RedactionAddon`：drop/mask/encrypt 字段级脱敏
- [ ] `AuditAddon` full-capture 模式：request/response body 采集与截断
- [ ] 流式支持：SSE、WebSocket、gRPC streaming 的 chunk 统计字段
- [ ] sandbox 镜像 CA 信任链改造（配合运维）
- [ ] 单元测试：脱敏规则、截断标记、TLS 降级模式
- [ ] 集成测试：HTTPS MITM 验证、SSE/WS/gRPC 转发与统计字段

**验收（AC-3、AC-4）**

### Phase 3：访问控制策略 + 灰度 + 可观测性（预计 1 周）

**目标**：完整策略引擎上线，全维度灰度，可观测性就绪。

- [ ] `PolicyAddon`：allow/deny 访问控制
- [ ] `PolicyEngine`：模式优先级匹配 + 热重载
- [ ] Prometheus 指标接入
- [ ] 告警规则配置
- [ ] 压测：长连接流式、高并发小包、大包截断
- [ ] 灰度扩量：按 user/experiment/namespace 维度开启 full-capture
- [ ] 文档：运维手册（CA 部署、iptables 权限要求）

**验收（AC-5、AC-7）**

---

## 10. 关键设计决策与权衡

### 10.1 mitmproxy 性能边界

mitmproxy 单进程 GIL 限制在高并发场景（>500 并发连接）下可能成为瓶颈。缓解方案：
- 按宿主机节点部署（节点级流量天然分散）
- 对 metadata-only 模式关闭 body 缓冲，仅做流量透传
- 如后续压测发现瓶颈，可将 mitmproxy 替换为基于 asyncio 的轻量代理（接口兼容，Addon 逻辑无需改动）

### 10.2 iptables 权限

注入 iptables 规则需要宿主机 root 或 `CAP_NET_ADMIN`。现有 `DockerDeployment` 已以 admin 权限运行（`--privileged` 模式），此处影响可控。若未来切换到非特权模式，可改用 eBPF/TC（需内核 5.8+）。

### 10.3 审计写入与转发路径解耦

`AuditWriter` 使用有界 `asyncio.Queue`，写入失败时仅告警、不阻断转发，满足 FR-9 和 NFR-7 中"优先保证转发路径可用"的要求。代价是极端情况下审计记录可能丢失，在本地日志场景属可接受权衡（生产环境应配合外部日志采集）。

### 10.4 身份映射缓存一致性

进程内 TTLCache（30s）在容器 IP 快速复用的极端场景下可能短暂误识别。缓解：sandbox 销毁时立即删除 Redis key + 刷新本地缓存；TTL 设置小于容器最小存活时间（实际 sandbox 存活 ≥ 若干分钟）。

---

## 11. 文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `rock/config.py` | 修改 | 新增 5 个配置 dataclass，`RockConfig` 新增 `egress_gateway` 字段 |
| `rock/env_vars.py` | 修改 | 新增 `ROCK_EGRESS_GATEWAY_ENABLED` |
| `rock/deployments/docker.py` | 修改 | start/stop 中注入/清理 iptables 规则及 Redis 身份映射 |
| `rock/admin/core/redis_key.py` | 修改 | 新增 `EGRESS_IDENTITY_PREFIX` 常量 |
| `rock/cli/main.py` | 修改 | 新增 `egress` 子命令 |
| `pyproject.toml` | 修改 | 新增 `egress` 可选依赖组，新增 `egress` entry point |
| `rock/egress/` | 新增 | 整个 egress 模块（约 10 个文件）|
| `rock-conf/rock-local.yml` | 修改（可选）| 新增 `egress_gateway` 示例配置块 |
