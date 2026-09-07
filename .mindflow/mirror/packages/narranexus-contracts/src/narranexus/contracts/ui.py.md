---
code_file: packages/narranexus-contracts/src/narranexus/contracts/ui.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2a）— `Theme`

`ui.themes` 位的契约：只列出要覆盖的设计 token（前端按 `@theme` 已声明集合校验），`dark` 标记配色族。

## 2026-09-03（批 1）— `ui` 位的 Python 侧契约

前端真正的贡献注册表在 TypeScript（`frontend/src/platform/registries`）；Python 侧只需要给 `ui`
扩展位一个可 import 的契约符号，让发行版能绑定自己的壳。`Shell` 是 backend 提供静态资源所需的
最小数据（构建目录 + 入口文件），纯数据、frozen，默认提供者 `builtin.ui`。

## 2026-09-07 — 前端 16 个注册表的条目形状镜像（B7）

Page/SidebarItem/Panel/SettingsSection/Command/ChannelConfig/MessageRenderer/TimelineEvent/ConversationKind/SlotComponent/SlotAction：builtin.ui 声明的 ui.* 槽的契约符号。TS 的 *Def 接口是真值；这里只承载 manifest frontend.ui 能表达的声明性子集（id/label/order/route），行为（组件、回调）只存在于 bundle。
