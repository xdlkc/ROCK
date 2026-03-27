# VNC Proxy — Interface Contract

## 1. HTTP VNC Proxy

### Endpoint

```
ANY /sandboxes/{sandbox_id}/proxy/vnc
ANY /sandboxes/{sandbox_id}/proxy/vnc/{path:path}
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sandbox_id` | path | ✅ | 沙箱标识 |
| `path` | path | ❌ | VNC 服务器目标路径，默认空字符串 |

### 支持的方法

- GET
- POST
- PUT
- DELETE
- PATCH
- HEAD
- OPTIONS

### 行为规则

- 所有请求固定转发到沙箱内 **port 8006**
- 路径透传：`/proxy/vnc/core/rfb.js` → `http://{sandbox}:8006/core/rfb.js`
- 查询参数透传：`/proxy/vnc/?resize=scale` → `http://{sandbox}:8006/?resize=scale`
- **忽略** `rock_target_port` 查询参数（VNC 路由固定使用 8006）

### 错误响应

| Status Code | 原因 | 场景 |
|-------------|------|------|
| 404 | `Sandbox not found` | sandbox_id 不存在 |
| 502 | `VNC server unreachable` | 沙箱未启动或 port 8006 不可达 |
| 500 | `Proxy error: ...` | 其他代理错误 |

### Examples

```bash
# 访问 noVNC 主页
GET /sandboxes/my-sandbox/proxy/vnc/

# 访问静态文件
GET /sandboxes/my-sandbox/proxy/vnc/core/rfb.js

# 带 query string
GET /sandboxes/my-sandbox/proxy/vnc/?resize=scale&reconnect=true

# POST API 请求
POST /sandboxes/my-sandbox/proxy/vnc/api/config
Content-Type: application/json
{"setting": "value"}
```

---

## 2. WebSocket VNC Proxy

### Endpoint

```
WS /sandboxes/{sandbox_id}/proxy/vnc/{path:path}
```

### 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sandbox_id` | path | ✅ | 沙箱标识 |
| `path` | path | ❌ | VNC WebSocket 目标路径，默认空字符串 |

### 行为规则

- 所有 WebSocket 连接固定转发到沙箱内 **port 8006**
- 路径透传：`/proxy/vnc/ws/websockify` → `ws://{sandbox}:8006/ws/websockify`
- **忽略** `rock_target_port` 查询参数
- 双向透明转发所有 WebSocket 帧

### WebSocket Close Codes

| Code | 原因 | 场景 |
|------|------|------|
| 1008 | `Port 8006 is not accessible` | VNC 服务不可达 |
| 1011 | `Proxy error: ...` | 其他代理错误 |

### Examples

```javascript
// 连接到 noVNC websockify
const ws = new WebSocket('ws://admin/sandboxes/my-sandbox/proxy/vnc/ws/websockify');

// 连接到自定义 WebSocket 路径
const ws = new WebSocket('ws://admin/sandboxes/my-sandbox/proxy/vnc/ws/custom-path');
```

---

## 3. 与通用 Proxy 的对比

| 特性 | 通用 Proxy (`/proxy`) | VNC Proxy (`/proxy/vnc`) |
|------|----------------------|--------------------------|
| 端口指定 | 通过 `rock_target_port` 查询参数 | **固定 port 8006** |
| 默认端口 | `Port.SERVER = 8080` | 无默认值（始终 8006） |
| 路由格式 | `/proxy/{path}` | `/proxy/vnc/{path}` |
| HTTP 方法 | ALL | ALL |
| WebSocket | ✅ | ✅ |
| 用途 | 通用代理 | VNC 专用 |

---

## 4. 典型使用场景

### 场景 1: 浏览器访问 noVNC

```html
<!-- 直接嵌入 iframe -->
<iframe src="https://admin/sandboxes/my-sandbox/proxy/vnc/"></iframe>
```

### 场景 2: noVNC JavaScript 客户端

```javascript
import RFB from '@novnc/novnc/core/rfb';

const url = 'wss://admin/sandboxes/my-sandbox/proxy/vnc/ws/websockify';
const rfb = new RFB(document.getElementById('screen'), url);
```

### 场景 3: API 访问

```bash
# 获取 VNC 配置
curl https://admin/sandboxes/my-sandbox/proxy/vnc/api/config

# 更新 VNC 设置
curl -X POST https://admin/sandboxes/my-sandbox/proxy/vnc/api/settings \
  -H "Content-Type: application/json" \
  -d '{"resolution": "1920x1080"}'
```