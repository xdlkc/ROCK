## Sandbox Egress Gateway 需求规格（重写版）

### 1. 背景与目标

沙箱内部会运行 agent 服务，agent 需要访问外部 LLM API（HTTP/HTTPS 及其他七层协议）。  
本方案新增一个 **Egress Gateway（出站网关）**，目标：

- **出站统一收口**：sandbox 内对外 HTTP/HTTPS 请求（以及其他七层协议）统一经由 Gateway。
- **统一审计**：Gateway 在转发点记录请求/响应日志（含流式场景）。
- **按维度灰度控制**：支持按 `user/experiment/namespace/sandbox` 灰度开启，默认兼容现有实现，可一键关闭。
- **安全合规**：提供访问控制、敏感信息脱敏、留存与删除能力。

本期设计重点针对 **Docker sandbox + 自管宿主机** 场景，可借鉴 Docker Sandboxes 的“统一出网代理 + 安全策略 + 凭据治理”思路，但不局限于其具体实现。

### 2. 需求边界

#### 2.1 本期范围

- 出站流量收口（HTTP/HTTPS 为主，兼顾其他常见七层协议）。
- 部署范围：**Docker sandbox + 自管宿主机**（可控制宿主机网络规则），优先采用“节点级 Gateway”形态。
- Gateway 审计采集（请求/响应，含流式）。
- 基础安全策略（脱敏、访问控制、留存/删除）。
- 可灰度开关与快速回退。

#### 2.2 非本期范围

- 非 HTTP 协议的精细治理（如原生 TCP/UDP 全功能代理）。
- Agent 业务逻辑改造。
- 新 LLM Provider 协议适配。
- 云厂商完全托管、无法控制宿主机网络规则的环境下，对“网络层强制收口”的强 SLA 保证：
  - 在此类环境中可提供 best-effort 代理能力，但不宣称“必须经 Gateway 才能出网”的强约束。

### 3. 核心术语

- `sandbox`：隔离运行环境。
- `agent`：沙箱内发起外部请求的服务进程。
- `egress gateway`：沙箱外发流量的统一出口。
- `audit log`：网关记录的请求/响应审计日志。

### 4. 用户故事

- 作为平台安全负责人，我需要知道每个 sandbox 对外请求了什么、何时请求、结果如何。
- 作为运维，我需要通过 `trace_id` 快速定位某次 LLM 调用失败原因。
- 作为产品方，我需要对高风险 `user/experiment` 开启全量审计，对低风险场景仅记录元数据。

### 5. 总体方案（逻辑）

1. **出站收口**：sandbox 内 agent 的 HTTP/HTTPS（及其他七层协议）请求强制经过 egress gateway。  
2. **统一审计**：gateway 在转发点采集 request/response 元数据及内容。  
3. **可控留存**：支持 `off` / `metadata-only` / `full-capture` 审计等级。  
4. **安全合规**：敏感字段脱敏/加密，支持 retention 与访问/删除审计。  
5. **灰度可回退**：按 sandbox/user/experiment/namespace/route 配置开关，异常时一键回退。

---

### 6. 功能需求（FR）

#### FR-1 出站强制经网关

- 系统必须支持将 sandbox 内 HTTP/HTTPS 出站统一导向 Gateway。
- 系统必须采用**网络层强制收口**（必须项），在 **Docker sandbox + 自管宿主机** 场景下通过宿主机 iptables / eBPF 等手段：
  - 仅允许 sandbox 访问 Gateway 出口；
  - 阻断 sandbox 直连外网 HTTP/HTTPS。
- 可额外叠加运行时代理注入作为辅助手段（如环境变量代理设置），但不得替代网络层强制收口。
- 对未经过 Gateway 的外发请求应尽量可观测并告警：
  - 该能力为**弱 SLA**：仅在具备底层网络观测能力（如 eBPF/iptables 审计）的节点上保证；
  - 不承诺在所有运行环境 100% 覆盖。

#### FR-2 请求/响应审计

- 每次出站调用至少记录以下元数据：
  - `timestamp`, `sandbox_id`, `user_id`, `experiment_id`, `namespace`, `trace_id`
  - `method`, `scheme`, `host`, `port`, `path`, `query`
  - `status_code`, `latency_ms`, `upstream_ip`
- 在 `full-capture` 模式下，还需记录：
  - `request_headers`, `request_body`
  - `response_headers`, `response_body`
- 支持请求体/响应体截断与大小上限配置（如 `max_body_bytes`）。

- HTTPS 明文捕获边界：
  - 本期必须支持对 HTTPS 流量的 TLS 终止/MITM：
    - Gateway 负责维护和发布内部根 CA；
    - ROCK 团队负责在 sandbox 镜像/运行环境中预置信任该 CA。
  - 启用 TLS 终止后，Gateway 对所有出站 HTTPS 请求进行 MITM，可在 `full-capture` 模式下捕获明文 request/response body，并仍按脱敏与截断规则处理。
  - 若某些环境暂未完成证书信任部署，则在该环境下：
    - `full-capture` 对 HTTPS 仅保证元数据与 TLS 会话信息，不保证明文 body；
    - 该行为视为“降级模式”，需在运维侧可观测。

- 扩展字段（可选）：
  - `tenant_id`（若业务侧存在租户概念，可在接入层映射到 user/experiment/namespace）。
  - `agent_id`（若 agent 服务显式上报）。

#### FR-3 流式请求支持（SSE/Chunked/WebSocket/gRPC）

- Gateway 必须支持常见 HTTP(S) 下的流式转发，不破坏流式语义，至少包括：
  - HTTP(S) + Server-Sent Events（SSE）。
  - HTTP Chunked streaming。
  - WebSocket（ws/wss）。
  - gRPC over HTTP/2。

- 对上述所有流式场景，审计至少记录：
  - `first_byte_latency_ms`：从接收请求到下游首字节到达的时间。
  - `stream_duration_ms`：从首字节到连接关闭/结束的时间。
  - `chunk_count`：数据块数量（对 WebSocket/gRPC 可按 message 计数）。
  - `total_bytes`：整个流式会话的总字节数（请求 + 响应可分开统计）。

- 在 `full-capture` 模式下：
  - **默认记录所有 chunk/message 的内容**，但整体仍受 `max_body_bytes` 等配置的总大小限制，超过即截断；
  - 截断后需在审计记录中显式标记 `truncated=true` 及截断原因。

#### FR-4 敏感信息保护

- 默认脱敏字段（包括但不限于）：
  - `authorization`, `x-api-key`, `apiKey`, `token`, `cookie`, `set-cookie`。
- 支持字段级策略：
  - `drop`（不存储该字段）。
  - `mask`（部分掩码，如仅保留前后若干位）。
  - `encrypt`（使用服务端密钥加密存储，需配合密钥管理方案）。
- 日志与错误返回中不得出现明文密钥或完整长 token。

#### FR-5 访问控制与策略

- 支持按目标域名/端口的 allow/deny 策略。
- 支持按以下维度配置策略：
  - `sandbox_id`
  - `user_id`
  - `experiment_id`
  - `namespace`
  - `route`（可为 URL 前缀或精确路径）
- 策略变更应在可接受时间内生效（见 NFR）。
- 策略默认行为：
  - 推荐默认策略为 `default_action: allow`；
  - 不预置隐含白名单，所有外部访问白名单应通过配置显式声明；
  - 当策略配置错误导致误杀时，建议回退路径为：
    - 切回 `metadata-only` 模式；
    - 将 `default_action` 暂时调整为 `allow`。

#### FR-6 模式与灰度

- 审计模式：
  - `off`：不记录（仅保留必要的计数型指标）。
  - `metadata-only`：仅记录元数据，不存储明文 body。
  - `full-capture`：记录请求/响应内容（按脱敏与截断规则）。

- 模式生效优先级（从高到低）：
  1. sandbox 级
  2. user/experiment/namespace 级
  3. route 级
  4. 全局默认

- 模式匹配规则：
  - 同一层级内如有多条规则匹配时，按配置顺序“先匹配先生效”；
  - 匹配维度为空时的行为：
    - 例如仅配置了 `user_id`，请求中缺失 `experiment_id`，只要 `user_id` 匹配即视为命中；
  - 高优先级层级对低优先级层级具有**一票否决**能力：
    - 一旦 sandbox 级有显式模式配置，则忽略该 sandbox 下 user/experiment/namespace 与 route 级模式。

- 模式切换需支持运行时动态更新，而不需要重启 Gateway。

#### FR-7 查询与排障

- 提供按以下维度检索审计日志（在启用集中式存储的场景下）：
  - `trace_id`, `sandbox_id`, `user_id`, `experiment_id`, `namespace`, `host`, `status_code`, `time_range`。
- 支持查看一条调用的完整链路摘要：
  - 请求概览。
  - 响应概览。
  - 重试信息（如由上层执行的重试，可通过 trace 关联）。
  - 错误分类与错误码。

> 注：在仅使用本地日志的场景下，FR-7 所述检索能力可通过日志采集+集中检索系统实现，不强制要求 Gateway 内建检索接口。

#### FR-8 错误语义

- 统一错误码：
  - `EGRESS_POLICY_DENIED`
  - `EGRESS_UPSTREAM_TIMEOUT`
  - `EGRESS_UPSTREAM_UNREACHABLE`
  - `EGRESS_GATEWAY_INTERNAL_ERROR`
- HTTP 映射：
  - `EGRESS_POLICY_DENIED` -> `403`
  - `EGRESS_UPSTREAM_TIMEOUT` -> `504`
  - `EGRESS_UPSTREAM_UNREACHABLE` -> `502`
  - `EGRESS_GATEWAY_INTERNAL_ERROR` -> `500`
- 重试语义（本期）：
  - Gateway **不在内部对 upstream 进行自动重试**；
  - 是否重试由上游（agent/SDK）根据业务幂等策略自行决定；
  - 错误返回中的 `retryable` 字段仅作为“建议信息”，不代表 Gateway 已经或将要重试。
- 错误返回应包含：
  - `trace_id`, `sandbox_id`, `error_code`, `message`, `retryable`。

#### FR-9 审计存储与删除

- **本期最小实现要求**：
  - Gateway 至少以**结构化本地日志**（如 JSON log）的形式落盘审计数据，覆盖 FR-2/FR-3/FR-10 中定义的关键字段；
  - 本地日志需配合 logrotate 或等价机制，支持按大小/时间滚动与粗粒度 retention；
  - 当本地日志写入失败（磁盘满、权限错误等）时：
    - Gateway 应**优先保证转发路径可用**，不因审计失败阻断业务请求；
    - 同时输出指标与告警，标记“审计降级”状态。

- **集中式审计存储（可选/后续阶段）**：
  - 审计存储分层：
    - 元数据索引层（用于检索）：支持按 `trace_id/sandbox_id/user_id/experiment_id/namespace/time` 查询；
    - Payload 存储层（用于 full-capture 内容）：对象存储或等价方案。
  - 索引要求：
    - `request_id` 唯一索引；
    - `trace_id` 普通索引（允许一对多记录）；
    - `sandbox_id + timestamp` 组合索引；
    - `user_id + experiment_id + timestamp` 组合索引。
  - 删除 SLA（仅在启用集中式存储后适用）：
    - 按作用域（`sandbox_id` 或 `user_id/experiment_id`）发起删除后，15 分钟内从检索结果不可见；
    - 24 小时内完成底层物理删除（或加密密钥销毁实现不可恢复）。

#### FR-10 身份来源可信模型

- Gateway 不得信任业务请求头中的 `sandbox_id/user_id/experiment_id/namespace` 作为审计与策略主依据。
- 身份主来源必须为宿主机网络身份映射（例如 `sandbox 网卡/IP/端口 -> sandbox_id` 的可信映射表），该映射由 sandbox operator/worker 在 sandbox 生命周期内维护：
  - sandbox 创建时，operator 在可信控制面（如 Redis/本机共享配置）中写入 `ip/port -> sandbox_id` 映射；
  - sandbox 销毁或网络资源回收时，operator 需及时清理对应映射，避免 IP 复用导致误识别。
- Gateway 从映射源按需拉取/缓存，并在缓存失效或冲突时以控制面数据为准。
- 业务请求头中的身份字段仅作辅助信息：
  - 若与可信映射冲突，以可信映射为准；
  - 同时记录安全告警与相关审计字段。
- 当 Gateway 无法根据网络映射可靠识别 sandbox 身份时：
  - 默认仍放行请求；
  - 在审计中标记 `identity_verified=false` 并输出相应告警指标。
- 每条审计记录必须包含：
  - `identity_source=network_mapping|header|mixed`
  - `identity_verified=true|false`
  - `request_id`（全局唯一）

---

### 7. 非功能需求（NFR）

- **NFR-1 性能（按模式）**：
  - `metadata-only`：网关新增 P95 延迟 ≤ 30ms（同机房基线）。
  - `full-capture`：网关新增 P95 延迟 ≤ 80ms（同机房基线）。

- **NFR-2 可用性**：
  - Gateway 月可用性 ≥ 99.9%。

- **NFR-3 策略生效时效**：
  - 配置变更生效时间 ≤ 10s（目标值）。

- **NFR-4 容量与流控**：
  - 支持高并发流式请求；
  - 连接池大小、并发上限、backpressure 策略可配置。

- **NFR-5 合规与留存**：
  - 仅本地日志场景：通过日志滚动与主机级策略提供粗粒度留存；
  - 启用集中式审计存储后：
    - 支持 retention（如 metadata 30 天、payload 7 天）；
    - 支持按 `sandbox_id` 与 `user_id/experiment_id` 删除，遵循 FR-9 中的可见性与物理删除 SLA。

- **NFR-6 流式连接资源约束**：
  - 支持配置最大流式连接数；
  - 支持配置单连接最大持续时长（建议默认 2 小时，可按环境调整）；
  - 支持 idle timeout（在一段时间无数据传输后主动关闭连接）。

- **NFR-7 观测与降级**：
  - Gateway 必须输出核心指标并接入现有监控栈（如 Prometheus/OTEL）：
    - 按错误码的请求数；
    - 策略拒绝次数；
    - 审计写入失败次数、本地日志丢弃次数；
    - 流式连接数、队列长度等资源使用指标。
  - 当审计写入路径（本地或集中式）不可用时：
    - 应优先保证转发路径可用；
    - 审计功能降级，并通过指标与告警反映。

---

### 8. 架构选型与本期决策

- 至少支持以下架构形态（允许未来混合）：
  1. **集中式 Egress Gateway**：单独部署网关集群，sandbox 出站统一汇入该集群。
  2. **节点级 Gateway**：每台宿主机部署本地 egress proxy，sandbox 就近接入。
  3. **Sidecar Gateway**：每个 sandbox/Pod 附带 sidecar 代理，负责本地出站与审计采集。

- 架构决策考量维度：吞吐、故障域、成本、隔离级别、运维复杂度。

- **本期具体决策**：
  - 优先采用 **节点级 Gateway** 形态：
    - 在每台宿主机部署本地 egress proxy，与该节点上的 sandbox worker 部署在同一台机器上；
    - 该节点上的 sandbox 通过本节点 Gateway 统一出站。
  - Gateway 作为**独立服务**部署：
    - 与 `admin` / `rocklet` 并列存在；
    - 由 ROCK 控制面统一管理配置与观测。
  - 集中式与 sidecar 形态作为后续阶段的可选扩展，在本期中无需完全实现，但整体设计需要预留扩展空间（如配置与服务发现接口保持通用）。

---

### 9. 配置草案与 ROCK 集成

示例配置：

```yaml
egress_gateway:
  enabled: true

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
      - match:
          route_prefix: "https://api.openai.com/v1/chat/completions"
        mode: metadata-only

  policy:
    default_action: allow
    allow_hosts:
      - "api.openai.com:443"
      - "model-runner.internal:12434"

  capture:
    max_body_bytes: 65536
    max_chunk_capture: 0  # 0 表示不按 chunk 数限制，仅受 max_body_bytes 控制
    redact_fields:
      - "authorization"
      - "x-api-key"
      - "apiKey"
      - "token"
      - "cookie"

  retention:
    # 本地日志场景由主机 logrotate 等机制控制，这里的配置用于集中式存储场景
    metadata_days: 30
    payload_days: 7
    delete_sla:
      invisible_within_minutes: 15
      physical_delete_within_hours: 24
```

在 ROCK 配置体系中的集成建议：

- 在 `RockConfig` 中将 `egress_gateway` 作为独立配置块挂载（例如顶层 `egress_gateway` 或 `runtime.egress_gateway`），由控制面统一加载。
- 引入环境变量开关 `ROCK_EGRESS_GATEWAY_ENABLED`：
  - 默认值为 `false`；
  - 在不改动配置文件的前提下用于全局启用/关闭 Gateway 功能；
  - 当与配置文件中的 `egress_gateway.enabled` 同时存在时，以显式配置为准。
- 默认所有 backend（本地 sandbox / Docker / K8s / Ray）从逻辑上都“需要接入” Gateway：
  - 可通过配置在不同 backend / namespace 维度控制是否实际启用 Gateway（用于渐进迁移）。

---

### 10. 验收标准（AC）

- **AC-1**：sandbox 内 agent 访问外部 LLM API 时，在审计日志中可按 `sandbox_id` + `trace_id` 检索到对应请求记录。
- **AC-2**：在 `metadata-only` 模式下，不存储明文 body，仅保留元数据字段。
- **AC-3**：在 `full-capture` 模式下，可查看脱敏后的 request/response 样本，且 HTTPS 场景 body 为明文（在已完成 TLS 信任改造的环境中）。
- **AC-4**：SSE / WebSocket / gRPC 流式请求可正常转发，且有 `first_byte_latency_ms` / `stream_duration_ms` / `chunk_count` / `total_bytes` 等统计字段。
- **AC-5**：策略拒绝请求时返回统一错误码（如 `EGRESS_POLICY_DENIED`），并可按 `trace_id` 检索到对应记录。
- **AC-6**：关闭 Gateway 功能（或切回 `off`/`metadata-only`）后，不影响现有调用链路可用性。
- **AC-7**：在本地日志写入异常时，请求仍可正常转发，且监控中能看到“审计降级”相关告警。

---

### 11. 测试要求

- **单元测试**：
  - 脱敏规则（`drop/mask/encrypt`）。
  - 模式匹配优先级（sandbox > user/experiment/namespace > route > 全局）。
  - 规则冲突时“先匹配先生效”的行为。
  - 错误码到 HTTP 状态码的映射。
  - 身份映射异常时的 `identity_verified=false` 行为。

- **集成测试**：
  - HTTP/HTTPS 出站经 Gateway 验证（含 TLS MITM 场景）。
  - allow/deny 策略验证（含 default_action 行为）。
  - SSE / WebSocket / gRPC 转发与统计字段验证。
  - `off` / `metadata-only` / `full-capture` 三种模式的行为。
  - 节点级部署下，多 sandbox 共用单一 Gateway 的隔离与审计准确性。

- **压测**：
  - 长连接流式场景（持续数十分钟以上）。
  - 高并发小包场景（短链接、低延迟）。
  - 大包截断场景（验证 `max_body_bytes` 生效与截断标记）。

---

### 12. 发布与回退

- **Phase 1**：在 canary sandbox 上开启 `metadata-only` 模式，验证网络收口与基础审计。
- **Phase 2**：对指定 `user/experiment/namespace` 开启 `full-capture`，重点验证 HTTPS MITM、脱敏与截断行为。
- **Phase 3**：扩大覆盖范围，评估默认模式与性能/容量影响。
- **回退机制**：
  - 运行时切回 `off` 或 `metadata-only`；
  - 将 `policy.default_action` 临时调整为 `allow`；
  - 保留基础计数指标，便于事后分析。

---

### 13. 与 Docker 方案的关系

- 可借鉴点：
  - 统一出网入口；
  - 出网策略控制（allow/deny、灰度控制）；
  - 凭据不进入 sandbox 的治理思路；
  - TLS 终止/MITM 的信任链部署经验。

- 不强绑定点：
  - ROCK 可按自身部署形态选择集中式、节点级或 sidecar 方案，本期仅落地节点级；
  - 审计深度与留存策略按 ROCK 内网合规要求定制；
  - 存储与检索技术栈可根据现有基础设施（日志平台、对象存储、OLAP/搜索引擎）灵活选型。

