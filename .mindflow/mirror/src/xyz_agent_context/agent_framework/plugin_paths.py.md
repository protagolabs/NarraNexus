---
code_file: src/xyz_agent_context/agent_framework/plugin_paths.py
last_verified: 2026-08-28
stub: false
---

# plugin_paths.py — 可选框架插件的落点与"是否已装"的单一真值

## 为什么存在

本地版（`bash run.sh` + 桌面 DMG）不再自带 Claude Code / Codex 两个重家伙，
改由用户在 设置 → 插件 里按需安装。两者都装进**同一棵用户可写目录树**
`~/.narranexus/plugins/`（绝不写进只读、已公证的 `.app`）：

```
~/.narranexus/plugins/
├── nodejs/  node_modules/.bin/claude   ← npm --prefix，Claude CLI 2.1.220
└── pyenv/   claude_agent_sdk/ openai_codex/  ← uv pip --target，SDK wheel
```

本模块是**纯**的：只做路径拼装 + 文件系统/`find_spec` 探测。不含版本 pin
（pin 跟安装器走）、不 import `backend`（铁律 #21）。三个消费方必须对这套
布局达成一致：惰性 driver 工厂（`agent_framework/__init__`）、Claude 二进制
解析器（`adapters/claude/cli_binary`）、后端安装/状态路由。

## 可用性 ≠ import

`framework_installed(name)` 回答"这个框架的代码在不在"而**不 import 它**：
查插件 `pyenv` 目录（文件系统）**或** 在普通环境里 `find_spec`（这样云端按
常规方式预装 SDK 时也报"已装"）。`nexus_power` 内置恒真；未知名恒假。

`_present_in_base` 单独抽成函数，是为了让测试能强制"未命中普通环境"分支，
只验 pyenv 那条路径。`find_spec` 只定位不执行，故后端进程调用它不会把 ~186MB
的 SDK 拉进内存。

## activate_pyenv 的接缝语义

本地/桌面模式 driver 在后端进程内 in-process 运行（`AGENT_EXECUTOR_URL` 未设），
所以 `import claude_agent_sdk` / `openai_codex` 发生在后端进程。`activate_pyenv()`
把 `pyenv` **append** 进 `sys.path`：

- **append 而非 insert(0)**：base 赢共享依赖（anthropic/openai 等按 base 版本），
  pyenv 只补插件独有的 wheel，避免版本影子。
- 幂等；pyenv 不存在时 no-op。

惰性工厂在 `import` SDK **之前**调它 → 本地模式**装完免重启**即可用。云端走
remote executor + 镜像预装，本模块不参与该路径。

## 测试

`tests/agent_framework/test_plugin_paths.py`：env 覆盖、路径拼装、nexus_power
恒真、未知名恒假、pyenv/base 两条命中路径、append 语义与幂等。删掉目标逻辑
任一条即变红。
