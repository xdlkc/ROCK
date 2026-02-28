# Terminal Settings - Implementation Plan

## Summary

为 `create_session` 接口添加终端信息配置能力，支持设置 TERM、COLUMNS、LINES、LANG 等参数。

## Architecture

### 改动范围

```
rock/
├── actions/
│   ├── sandbox/
│   │   └── request.py          # [修改] 添加终端参数
│   └── __init__.py             # [检查] 导出确认
└── rocklet/
    └── local_sandbox.py        # [修改] BashSession.start() 处理参数
```

### 数据流

```
CreateBashSessionRequest
       │
       ▼
┌──────────────────────────────┐
│  term="xterm-256color"       │
│  columns=80                  │
│  lines=24                    │
│  lang="en_US.UTF-8"          │
└──────────────────────────────┘
       │
       ▼
BashSession.start()
       │
       ├──► env["TERM"] = term
       ├──► env["COLUMNS"] = str(columns)
       ├──► env["LINES"] = str(lines)
       ├──► env["LANG"] = lang
       │
       ▼
pexpect.spawn(
    command,
    env=env,
    dimensions=(lines, columns),  # 新增
    ...
)
```

## Implementation Steps

### Step 1: 修改 CreateBashSessionRequest

**文件**: `rock/actions/sandbox/request.py`

**改动**:
```python
class CreateBashSessionRequest(BaseModel):
    session_type: Literal["bash"] = "bash"
    session: str = "default"
    startup_source: list[str] = []
    env_enable: bool = False
    env: dict[str, str] | None = Field(default=None)
    remote_user: str | None = Field(default=None)

    # 新增终端参数
    term: str = Field(default="xterm-256color")
    """Terminal type (TERM environment variable)."""

    columns: int = Field(default=80, ge=1)
    """Terminal width in columns. Must be positive."""

    lines: int = Field(default=24, ge=1)
    """Terminal height in lines. Must be positive."""

    lang: str = Field(default="en_US.UTF-8")
    """Language and encoding (LANG environment variable)."""
```

**验证点**:
- [ ] Pydantic 验证 `columns >= 1` 和 `lines >= 1`
- [ ] 默认值正确设置

---

### Step 2: 修改 BashSession.start()

**文件**: `rock/rocklet/local_sandbox.py`

**改动**:

1. 设置终端环境变量:
```python
async def start(self) -> CreateBashSessionResponse:
    if self.request.env_enable:
        env = os.environ.copy()
    else:
        env = {}

    # 设置 shell 提示符
    env.update({"PS1": self._ps1, "PS2": "", "PS0": ""})

    # 新增：设置终端环境变量
    env["TERM"] = self.request.term
    env["COLUMNS"] = str(self.request.columns)
    env["LINES"] = str(self.request.lines)
    env["LANG"] = self.request.lang

    # 用户自定义 env 优先级最高
    if self.request.env is not None:
        env.update(self.request.env)

    # ... 后续代码
```

2. 设置 pexpect 窗口尺寸:
```python
self._shell = pexpect.spawn(
    command,
    encoding="utf-8",
    codec_errors="backslashreplace",
    echo=False,
    env=env,
    maxread=self.request.max_read_size,
    dimensions=(self.request.lines, self.request.columns),  # 新增
)
```

**验证点**:
- [ ] 环境变量正确设置
- [ ] pexpect dimensions 参数正确传递
- [ ] env 参数优先级正确（覆盖专用参数）

---

### Step 3: 添加单元测试

**文件**: `tests/unit/rocklet/test_local_sandbox_runtime.py` (或新建)

**测试用例**:

| 测试 | 描述 |
|------|------|
| `test_default_terminal_settings` | 验证默认值 TERM/columns/lines/lang |
| `test_custom_terminal_settings` | 验证自定义值 |
| `test_stty_size_output` | 验证 `stty size` 输出正确 |
| `test_env_overrides_term_param` | 验证 env 参数优先级 |
| `test_invalid_columns_validation` | 验证 columns < 1 被拒绝 |
| `test_invalid_lines_validation` | 验证 lines < 1 被拒绝 |

---

### Step 4: 更新类型导出 (如需要)

**文件**: `rock/actions/__init__.py`

**检查**: 确认 `CreateBashSessionRequest` 已正确导出

---

## Task Breakdown

| # | 任务 | 预估时间 | 依赖 |
|---|------|----------|------|
| 1 | 修改 `CreateBashSessionRequest` 添加终端参数 | 15min | - |
| 2 | 修改 `BashSession.start()` 设置环境变量和 dimensions | 20min | #1 |
| 3 | 编写单元测试 | 30min | #2 |
| 4 | 运行测试验证 | 10min | #3 |
| 5 | 代码审查和清理 | 15min | #4 |

**总预估**: ~1.5 小时

## Testing Strategy

### 单元测试

```bash
# 运行相关单元测试
uv run pytest tests/unit/rocklet/test_local_sandbox_runtime.py -v

# 运行所有快速测试
uv run pytest -m "not need_ray and not need_admin and not need_admin_and_network" --reruns 1
```

### 手动验证

```python
from rock.actions import CreateBashSessionRequest, BashAction
from rock.rocklet.local_sandbox import LocalSandboxRuntime

async def verify():
    runtime = LocalSandboxRuntime()

    # 测试默认值
    await runtime.create_session(CreateBashSessionRequest(session="test1"))
    obs = await runtime.run_in_session(BashAction(session="test1", command="echo $TERM && stty size"))
    print(obs.output)  # 期望: xterm-256color, 24 80

    # 测试自定义值
    await runtime.create_session(CreateBashSessionRequest(
        session="test2",
        term="screen",
        columns=120,
        lines=40
    ))
    obs = await runtime.run_in_session(BashAction(session="test2", command="echo $TERM && stty size"))
    print(obs.output)  # 期望: screen, 40 120
```

## Risks & Mitigations

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 向后兼容性破坏 | 高 | 所有新参数有默认值，现有代码无需修改 |
| pexpect dimensions 行为差异 | 中 | 编写 stty size 测试验证实际行为 |
| 某些程序忽略 COLUMNS/LINES | 低 | 同时设置环境变量和 pexpect dimensions |

## Rollback Plan

如果发现问题，可以：
1. 回滚此分支的改动
2. 新参数有默认值，不影响现有用户

## References

- Spec: `docs/specs/terminal-settings-spec.md`
- pexpect spawn 文档: https://pexpect.readthedocs.io/en/stable/api/pexpect.html#pexpect.spawn
