# VNC Proxy — Requirement Spec

## Background

ROCK 沙箱通常使用 VNC（noVNC）作为图形界面访问方式。当前用户访问 VNC 需要使用通用 proxy 路由并指定端口：

```
GET /sandboxes/{sandbox_id}/proxy/?rock_target_port=8006
WS /sandboxes/{sandbox_id}/proxy/ws?rock_target_port=8006
```

这种方式的限制：
- 用户需要记住 VNC 固定端口 8006
- 每次访问都需要指定 `rock_target_port=8006`
- URL 较长，不够直观

---

## In / Out

### In（本次要做的）

1. **VNC 专用路由**
   - 新增 `/sandboxes/{sandbox_id}/proxy/vnc[/{path}]` 路由
   - 固定转发到沙箱内 port 8006
   - 无需用户指定端口参数

2. **HTTP VNC Proxy**
   - 支持所有 HTTP 方法（GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS）
   - 透传请求到 VNC 服务器
   - 支持 noVNC web 界面访问

3. **WebSocket VNC Proxy**
   - 支持 WebSocket 长连接
   - 支持 noVNC websockify 协议
   - 双向透明转发

### Out（本次不做的）

- 自动检测 VNC 端口
- 多 VNC 端口配置
- VNC 特定参数注入（如 autoconnect）
- Location header 重写（除非实际遇到问题）

---

## Acceptance Criteria

- **AC1**：`GET /sandboxes/{sandbox_id}/proxy/vnc/` 能成功代理到沙箱内 port 8006 的 VNC 服务
- **AC2**：`GET /sandboxes/{sandbox_id}/proxy/vnc/core/rfb.js` 能成功代理静态文件
- **AC3**：`WS /sandboxes/{sandbox_id}/proxy/vnc/ws/websockify` 能成功建立 WebSocket 连接
- **AC4**：VNC 路由忽略 `rock_target_port` 参数，始终使用 port 8006
- **AC5**：所有 HTTP 方法（GET/POST/PUT/DELETE/PATCH/HEAD/OPTIONS）均能正常工作
- **AC6**：沙箱不存在或 port 8006 不可达时返回正确错误（404 或 502）