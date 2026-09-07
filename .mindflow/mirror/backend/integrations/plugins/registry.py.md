---
code_file: backend/integrations/plugins/registry.py
last_verified: 2026-09-07
stub: false
---

# registry.py — 可安装框架插件的派生表

## 2026-09-03（批 1）— `build_plugin_specs()` 从框架注册表按需派生（无 import 期快照）

不再手写两条 `PluginSpec`，也不再有模块级 `PLUGIN_SPECS` 常量：遍历 `FRAMEWORK_REGISTRY.entries()`，
`meta["framework"].install` 非空的框架各生成一条。去掉 import 期快照是因为注册表可在启动后继续
注册框架插件，快照会漏掉后注册者；`PluginService` 在构造时调用它。钉版本的唯一来源移到 `agent_framework/__init__.py`；
`tests/backend/integrations/plugins/test_registry.py` 的全部断言（键集、组件、pin 与 uv.lock 锁步）
原样通过。

## 为什么存在

`build_plugin_specs()` 是"装什么"这个问题唯一的答案来源。Phase 3 的路由、
`service.PluginService`（默认参数）都从它取 dict,不接受任何一方
自己再列一遍两个插件叫什么名字、钉了哪个版本——那样版本号迟早会在两个
地方走岔。

## 上下游关系

- **被谁用**：`service.PluginService.__init__` 默认调用它初始化
  `self._specs`;Phase 3 路由（未实现）会直接 import `PLUGIN_SPECS` 渲染
  插件列表页。
- **依赖谁**：`narranexus_plugins.frameworks_claude_code.
  cli_binary.PINNED_CLI_VERSION`——Claude 的 npm 版本号从这里拼,不是字面
  量。`spec.py` 提供 dataclass 形状。

## 设计决策

- claude-agent-sdk 的 pip 版本（`0.1.43`）和 openai-codex 的 pip 版本
  （`0.1.0b3`）**是**字面量,因为它们分别对应 `pyproject.toml` 的
  `claude-agent-sdk~=0.1.43` 和 `openai-codex>=0.1.0b3,<0.2`——pyproject
  用的是范围约束（给 `uv sync` 的弹性）,而插件安装器需要一个具体版本去
  请求 pip,两者语义不同,不能互相 import,只能人工保持同步（改
  `pyproject.toml` 的下限时记得同步改这里）。
- Claude 的 npm requirement 唯独**不是**字面量,而是从
  `PINNED_CLI_VERSION` 拼接——因为这个常量本身就是 agent loop 实际选择
  运行哪个 CLI 二进制的单一真值（见 `cli_binary.py` 的设计说明:2.1.56 vs
  2.1.220 在工具排序上行为不同,直接影响 prompt cache 命中率）。插件装的
  必须和 agent loop 会用的是同一个版本,这条线不能断。

## Gotcha / 边界情况

- **触发**：升级 `pyproject.toml` 里 `claude-agent-sdk` 或 `openai-codex`
  的版本下限时 → **症状**：插件商店会继续给用户装旧版本,新装的插件运行时
  和后端代码实际期望的 SDK 版本不一致 → **根因**：这两个 pip 版本是手抄的
  字面量,不是从 pyproject 动态解析的（动态解析 `pyproject.toml` 会引入对
  TOML 解析器的运行时依赖,且 pyproject 的范围约束本身也不能唯一确定"该装
  哪个具体版本",人工同步是更简单也更诚实的选择）。

## 相关约束

- `cli_binary.py` 的 `PINNED_CLI_VERSION` docstring —— 版本单一真值的完整
  上下文

Batch 6b.2b: `build_plugin_specs()` calls `ensure_builtin_frameworks()` (lazy manifest registration) instead of importing the platform package; the install pins live in each `narranexus_plugins.frameworks_*` package.

## 2026-09-07 — _spec_from_meta 透传 login_marker（B6）

PluginSpec.login_marker 来自 meta.login_marker。
