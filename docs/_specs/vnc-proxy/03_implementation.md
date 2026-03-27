# VNC Proxy — Implementation Plan

## 背景

VNC proxy 是在现有 proxy infrastructure 基础上的扩展，为 VNC 访问提供专用路由。关键设计：
- **固定端口转发**：所有 `/proxy/vnc` 请求固定转发到 port 8006
- **复用现有基础设施**：不需要修改 service 层，仅添加新路由

---

## File Changes

| 文件 | 修改类型 | 说明 |
|------|------|------|
| `rock/admin/entrypoints/sandbox_proxy_api.py` | **新增** | 添加 VNC HTTP 和 WebSocket 路由 |
| `tests/unit/sandbox/test_proxy_enhancements.py` | **新增** | 添加 VNC proxy 单元测试 |

---

## 核心逻辑

### 变更 1：添加 HTTP VNC Proxy 路由

```python
@sandbox_proxy_router.api_route(
    "/sandboxes/{sandbox_id}/proxy/vnc",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
@sandbox_proxy_router.api_route(
    "/sandboxes/{sandbox_id}/proxy/vnc/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
@handle_exceptions(error_message="vnc http proxy failed")
async def vnc_http_proxy(
    sandbox_id: str,
    request: Request,
    path: str = "",
):
    body = None
    if request.method not in ("GET", "HEAD", "DELETE", "OPTIONS"):
        try:
            body = await request.json()
        except Exception:
            body = None
    return await sandbox_proxy_service.http_proxy(
        sandbox_id, path, body, request.headers, method=request.method, port=8006
    )
```

**要点：**
- 固定 `port=8006`，不读取 query parameter
- 支持所有 HTTP 方法
- 复用现有 `SandboxProxyService.http_proxy()` 方法

---

### 变更 2：添加 WebSocket VNC Proxy 路由

```python
@sandbox_proxy_router.websocket("/sandboxes/{sandbox_id}/proxy/vnc/{path:path}")
async def vnc_websocket_proxy(
    websocket: WebSocket,
    sandbox_id: str,
    path: str = "",
):
    logger.info(f"Client connected to VNC WebSocket proxy: {sandbox_id}, path: {path}")
    try:
        await sandbox_proxy_service.websocket_proxy(websocket, sandbox_id, path, port=8006)
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from VNC WebSocket proxy: {sandbox_id}")
    except Exception as e:
        logger.error(f"VNC WebSocket proxy error: {e}")
        await websocket.close(code=1011, reason=f"Proxy error: {str(e)}")
```

**要点：**
- 固定 `port=8006`
- 复用现有 `SandboxProxyService.websocket_proxy()` 方法
- 统一的路由处理 HTTP 和 WebSocket（FastAPI 根据 Upgrade header 自动判断）

---

### 变更 3：Service 层无需修改

`SandboxProxyService.http_proxy()` 和 `SandboxProxyService.websocket_proxy()` 已支持 `port` 参数：

```python
# 已有方法签名
async def http_proxy(
    self,
    sandbox_id: str,
    target_path: str,
    body: dict | None,
    headers: Headers,
    method: str = "POST",
    port: int | None = None,  # 支持自定义端口
    proxy_prefix: str | None = None,
    query_string: str = "",
) -> JSONResponse | StreamingResponse | Response:
    # ...
```

因此，VNC proxy 仅需在 API 层添加路由，无需修改 service 层。

---

## 测试策略

### Unit Tests

添加到 `tests/unit/sandbox/test_proxy_enhancements.py`：

```python
class TestVncHttpProxy:
    """VNC HTTP proxy should forward requests to fixed port 8006."""

    async def test_vnc_route_forwards_to_port_8006(self, app):
        """GET /proxy/vnc/ should forward to port 8006."""
        # ...

    async def test_vnc_route_preserves_path(self, app):
        """GET /proxy/vnc/core/rfb.js should forward path='core/rfb.js'."""
        # ...

    async def test_vnc_route_ignores_query_param_port(self, app):
        """VNC proxy should ignore rock_target_port query param."""
        # ...


class TestVncWebSocketProxy:
    """VNC WebSocket proxy should forward connections to fixed port 8006."""

    async def test_vnc_ws_route_forwards_to_port_8006(self, app):
        """WS /proxy/vnc/ws should forward to port 8006."""
        # ...
```

### Integration Tests

暂不添加（现有 proxy 测试已覆盖基本场景）

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| VNC 服务不在 port 8006 | 低 | 中 | 文档明确说明端口要求 |
| 与其他路由冲突 | 低 | 高 | 路由顺序：`/proxy/vnc` 优先于 `/proxy` |
| 性能影响 | 极低 | 低 | 仅添加路由，无额外逻辑 |

---

## 部署检查清单

- [x] 添加 VNC HTTP 路由到 `sandbox_proxy_api.py`
- [x] 添加 VNC WebSocket 路由到 `sandbox_proxy_api.py`
- [x] 添加单元测试
- [ ] 更新 API 文档
- [ ] 更新用户指南