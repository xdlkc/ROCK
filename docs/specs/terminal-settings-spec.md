# Terminal Settings for create_session - Feature Specification

> Issue: alibaba/ROCK#540

## Overview

为 `create_session` 接口添加终端信息配置能力，允许用户在创建 bash session 时设置终端类型、尺寸、字符编码等参数。

## Problem Statement

当前 `create_session` 接口创建的 bash session 缺乏终端相关配置：

1. **默认无终端环境变量**：当 `env_enable=False`（默认值）时，bash 进程没有 `TERM`、`COLUMNS`、`LINES` 等环境变量
2. **依赖终端的程序行为异常**：缺少 `TERM` 变量会导致：
   - 颜色输出失效
   - 文本编辑器（vim、nano）无法正常工作
   - 某些 CLI 工具（如 htop、less）报错或功能受限
3. **无法配置终端尺寸**：`stty size` 返回 0 或错误值

## User Stories

### US-1: 设置终端类型

**作为** SDK 用户
**我希望** 在创建 session 时指定终端类型
**以便** 让依赖终端的程序正常工作

**验收标准：**
- GIVEN 一个 Sandbox 实例
- WHEN 调用 `create_session(term="xterm-256color")`
- THEN session 中的 `echo $TERM` 输出 `xterm-256color`

### US-2: 设置终端尺寸

**作为** SDK 用户
**我希望** 在创建 session 时指定终端尺寸
**以便** 程序能正确处理行/列布局

**验收标准：**
- GIVEN 一个 Sandbox 实例
- WHEN 调用 `create_session(columns=120, lines=40)`
- THEN session 中的 `stty size` 输出 `40 120`

### US-3: 设置字符编码

**作为** SDK 用户
**我希望** 在创建 session 时指定字符编码
**以便** 正确处理多字节字符（如中文）

**验收标准：**
- GIVEN 一个 Sandbox 实例
- WHEN 调用 `create_session(lang="en_US.UTF-8")`
- THEN session 中的 `echo $LANG` 输出 `en_US.UTF-8`

### US-4: 默认行为（向后兼容）

**作为** SDK 用户
**我希望** 创建 session 时不设置 TERM/LANG 环境变量
**以便** 保持向后兼容，避免改变现有程序行为

**验收标准：**
- GIVEN 一个 Sandbox 实例
- WHEN 调用 `create_session()` 不传任何终端参数
- THEN session 使用以下默认值：
  - `TERM` - 不设置（保持原有行为）
  - `COLUMNS=80`
  - `LINES=24`
  - `LANG` - 不设置（保持原有行为）

## Functional Requirements

### FR-1: 终端类型参数 (term)

**WHEN** 用户在 `CreateBashSessionRequest` 中设置 `term` 参数
**THEN THE SYSTEM SHALL** 将该值设置为 bash 进程的 `TERM` 环境变量

- 类型：`str | None`
- 默认值：`None`（不设置 TERM 环境变量）
- 可选值：任意有效的终端类型字符串（如 `xterm`、`screen`、`vt100`、`xterm-256color`）

### FR-2: 终端宽度参数 (columns)

**WHEN** 用户在 `CreateBashSessionRequest` 中设置 `columns` 参数
**THEN THE SYSTEM SHALL**：
1. 设置 `COLUMNS` 环境变量
2. 设置 pexpect 的窗口宽度

- 类型：`int`
- 默认值：`80`
- 约束：必须为正整数

### FR-3: 终端高度参数 (lines)

**WHEN** 用户在 `CreateBashSessionRequest` 中设置 `lines` 参数
**THEN THE SYSTEM SHALL**：
1. 设置 `LINES` 环境变量
2. 设置 pexpect 的窗口高度

- 类型：`int`
- 默认值：`24`
- 约束：必须为正整数

### FR-4: 字符编码参数 (lang)

**WHEN** 用户在 `CreateBashSessionRequest` 中设置 `lang` 参数
**THEN THE SYSTEM SHALL** 将该值设置为 bash 进程的 `LANG` 环境变量

- 类型：`str | None`
- 默认值：`None`（不设置 LANG 环境变量）

### FR-5: 参数优先级

**WHEN** 用户同时设置 `term`/`columns`/`lines`/`lang` 参数和 `env` 参数
**THEN THE SYSTEM SHALL** 让 `env` 参数中的同名变量优先（`env` 覆盖专用参数）

### FR-6: env_enable 交互

**WHEN** `env_enable=True` 且用户设置了终端参数
**THEN THE SYSTEM SHALL**：
1. 先复制宿主机环境变量
2. 应用终端参数的默认值
3. 应用 `env` 参数中的自定义值

## Non-Functional Requirements

### NFR-1: 向后兼容

**THE SYSTEM SHALL** 保持现有 API 的向后兼容性：
- 现有不使用终端参数的代码继续正常工作
- 新参数都有合理的默认值

### NFR-2: 性能影响

**THE SYSTEM SHALL NOT** 对 session 创建性能产生可感知的影响：
- 新增的环境变量设置应在微秒级别完成

## Affected Components

### Primary Changes

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `rock/actions/sandbox/request.py` | 修改 | 添加终端参数到 `CreateBashSessionRequest` |
| `rock/rocklet/local_sandbox.py` | 修改 | `BashSession.start()` 处理终端参数 |

### Secondary Changes

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `rock/actions/__init__.py` | 可能修改 | 导出新参数 |
| `tests/unit/rocklet/test_local_sandbox_runtime.py` | 修改 | 添加测试用例 |

## API Changes

### CreateBashSessionRequest 新增字段

```python
class CreateBashSessionRequest(BaseModel):
    session_type: Literal["bash"] = "bash"
    session: str = "default"
    startup_source: list[str] = []
    env_enable: bool = False
    env: dict[str, str] | None = Field(default=None)
    remote_user: str | None = Field(default=None)

    # === 新增字段 ===
    term: str | None = Field(default=None)
    """Terminal type (TERM environment variable). If None, TERM is not set."""

    columns: int = Field(default=80, ge=1)
    """Terminal width in columns. Must be positive."""

    lines: int = Field(default=24, ge=1)
    """Terminal height in lines. Must be positive."""

    lang: str | None = Field(default=None)
    """Language and encoding (LANG environment variable). If None, LANG is not set."""
```

## Edge Cases

### EC-1: 无效的终端尺寸

**WHEN** 用户传入 `columns=0` 或 `lines=0`
**THEN THE SYSTEM SHALL** 使用默认值并记录警告日志

### EC-2: 负数尺寸

**WHEN** 用户传入负数的 `columns` 或 `lines`
**THEN THE SYSTEM SHALL** 抛出 `ValueError` 或使用 Pydantic 验证拒绝

### EC-3: 空 TERM 字符串

**WHEN** 用户传入 `term=""`
**THEN THE SYSTEM SHALL** 使用默认值 `"xterm-256color"`

## Test Scenarios

### TS-1: 默认值验证（向后兼容）

```python
async def test_terminal_default_values():
    runtime = LocalSandboxRuntime()
    response = await runtime.create_session(CreateBashSessionRequest(session="test"))

    # TERM 和 LANG 默认不设置
    obs = await runtime.run_in_session(BashAction(session="test", command="echo $TERM"))
    assert obs.output.strip() == ""

    obs = await runtime.run_in_session(BashAction(session="test", command="echo $LANG"))
    assert obs.output.strip() == ""

    # 终端尺寸仍然有默认值
    obs = await runtime.run_in_session(BashAction(session="test", command="stty size"))
    assert "24 80" in obs.output
```

### TS-2: 自定义值验证

```python
async def test_terminal_custom_values():
    runtime = LocalSandboxRuntime()
    response = await runtime.create_session(
        CreateBashSessionRequest(
            session="test",
            term="screen",
            columns=120,
            lines=40,
            lang="zh_CN.UTF-8"
        )
    )

    obs = await runtime.run_in_session(BashAction(session="test", command="echo $TERM"))
    assert "screen" in obs.output

    obs = await runtime.run_in_session(BashAction(session="test", command="stty size"))
    assert "40 120" in obs.output

    obs = await runtime.run_in_session(BashAction(session="test", command="echo $LANG"))
    assert "zh_CN.UTF-8" in obs.output
```

### TS-3: env 参数覆盖

```python
async def test_env_overrides_terminal_params():
    runtime = LocalSandboxRuntime()
    response = await runtime.create_session(
        CreateBashSessionRequest(
            session="test",
            term="xterm",
            env={"TERM": "vt100"}  # 应该覆盖 term 参数
        )
    )

    obs = await runtime.run_in_session(BashAction(session="test", command="echo $TERM"))
    assert "vt100" in obs.output
```

## Open Questions

1. **Q1**: 是否需要支持动态修改终端尺寸？（如 `resize_session` API）
   - **建议**: 暂不支持，作为后续需求

2. **Q2**: 是否需要验证 `term` 值是否为有效的 terminfo 类型？
   - **建议**: 不验证，允许任意字符串

3. **Q3**: `COLORTERM` 是否需要作为单独参数？
   - **建议**: 暂不添加，用户可通过 `env` 参数设置

## References

- pexpect documentation: https://pexpect.readthedocs.io/en/stable/api/pexpect.html#spawn-class
- terminfo documentation: https://man7.org/linux/man-pages/man5/terminfo.5.html
