---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/
stub: false
last_verified: 2026-11-19
---

## Why it exists

NarraMessenger（前身 NexusMatrix）是 NetMind 基于 Matrix 的 IM 平台。这个包把它接成一个 IM 渠道，
和 lark / slack / telegram 一样坐在通用的 `channel_*_base` 抽象上，以 `builtin.channels.narramessenger`
插件的形式发布（清单见包外的 `narranexus-plugin.json`）。

## Design decisions

- **传输是 Matrix 原生（Direct Matrix）。** trigger 用 matrix-nio 对 homeserver 做持续的 `/sync`
  long-poll 收消息，回复和主动外发都是 `room_send`。早期版本走过 Narra 的 Gateway 轮询 + `/chat/send`
  （纯 HTTP、没有 Matrix client），2026-07 起整体换成 Matrix，Gateway trigger 已删除；
  代码或旧文档里若还提到 Gateway 传输，都是历史。
- **每条要回复的消息先过 Narra 的 `authorize-event`**，失败即拒（fail-closed）。群里不 @ 的消息只进记忆、不回复。
- **房间是不是 1:1** 先看 Narra 写的 `ai.netmind.room_creation.v1` 状态，再退回到「有效成员数」
  （要排除每个托管房间里都有的 appservice 机器人）。
- **附件走 Matrix 媒体**：入站经鉴权下载后落到 workspace，出站用 `narra_send_media`。
- **绑定用 claim**：`narra_bind` 向 Narra 领取一次凭据（secret token + Matrix 身份），不从 setup guide 里抓。
- **narra-cli 由平台提供**：模型只通过 `narra_cli` MCP 工具调用，token 和 endpoint 按绑定注入，
  模型自己不安装、不配置。
- **语音通话**是一个独立子系统，在 `voice/` 子包里（Narra RTC 语音契约，当前做到 v15 的回复绑定和控制面）。

## Files

只列入口；私有实现（`_*.py`）的设计理由见各自的 mirror，不在这里重复——这份清单因此不会每加一个文件就过期。

- `matrix_trigger.py` — 渠道 trigger（`ChannelTriggerBase`）：`/sync` 循环、分类与鉴权、流式回复。
  运行时由 `descriptor.py` 的 `trigger_ref` 发现，**路径是部署契约的一部分**。
- `narramessenger_module.py` — 渠道 module（`ChannelModuleBase`）：对模型的说明、`send_to_agent`、回复提取。
- `narramessenger_context_builder.py` — 每轮的 prompt 组装和 turn envelope。
- `descriptor.py` — 渠道描述符：凭据 schema、回复工具登记、trigger / service 的引用。
- `contribution.py` — 清单里点名的贡献表（modules / triggers）。
- `routes.py` / `plugin_hooks.py` — 后端路由（绑定、状态、语音预热）和后端 hook。
- `api.py` — 这个插件包对其他包公开的 facade。
- `voice/` — 语音通话子系统，见 `voice/__init__.py.md`。
- `resources/` — 随包分发的静态资源（精简版 narra-cli 命令参考）。

MCP server 名是 `narramessenger_module`。端口不在这个包里声明。
