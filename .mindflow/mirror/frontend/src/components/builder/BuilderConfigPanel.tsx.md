---
code_file: frontend/src/components/builder/BuilderConfigPanel.tsx
last_verified: 2026-09-03
stub: false
---

# BuilderConfigPanel.tsx — 「对话把面板填好」的那半边

## 这里没有草稿

面板反映的是**真实 agent**。studio 跑在用户刚建出来的那个 agent 上，所以身份和
指令直接从 agent 读、直接写回去 —— 没有暂存区要对账，这两类字段也没有「应用」
这一步。这正是这条路径的意义。

## 为什么不内嵌那三个现成面板

`AwarenessPanel` / `SkillsPanel` / `IMChannelsSection` 本身就是抽屉的原子 tab。
把三个重面板叠进第四个里，既违反「一个 tab 一个 panel」的 IA（Owner
2026-06-11 定案），也让这个 tab 一次拉三份 lazy chunk。所以 studio 只呈现**对话
真正驱动的字段**，深度操作（配 skill 参数、贴 bot token）交回那些 tab。

## 三条约束

- **文本字段 blur 时才存**，不是每次按键。逐字符 PUT 会和模型对同一字段的写入
  竞争。
- **Skills / Channel 是推荐 + 人点**。理由见 [[builderApply.ts]]（装了又卸、
  凭证不能进模型）。渠道那一行的按钮是**跳到 Channels tab**，这里不收集任何
  凭证。
- **没有「放弃」**。面板里每个字段都已经写进 agent 了，没有可回滚的东西；
  「完成」只是离开 studio（[[builderSession.ts]]）。

## Gotcha

三个本地镜像 state 都有 effect 跟随服务端值 —— 这正是模型的写入能在面板里显现
的机制（`refreshAgents` / `refreshAwareness` 之后 store 变，effect 跟上）。
