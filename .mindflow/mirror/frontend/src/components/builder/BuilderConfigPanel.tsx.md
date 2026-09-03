---
code_file: frontend/src/components/builder/BuilderConfigPanel.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 (改版，Owner 参考稿) — 去头像/描述，补 Skills 与 Channel

版式按 Owner 的参考稿走：

- **身份只剩名称。** 头像去掉 —— 全项目没有 agent 头像能力，侧边栏那个是按
  identity 生成的色块，画一个「更换头像」等于暗示一个不存在的功能。
- **描述字段去掉，但对话仍然会写它。** 它是**给机器读的**：别的 agent 靠它判断
  要不要把任务路由过来（见 [[builderProtocol.ts]] 的指令），人看的地方是 Agent
  Profile 页。所以从这个面板移除，不代表停止写入。
- **「指令」改叫「认知」**，对齐它真正写的那个字段（awareness），不再用同义词。
- **Skills / Channel 补上**，而且是**内嵌抽屉自己那两个 section**
  （`SkillsPanel section="skills"` / `AwarenessPanel section="channels"`）。

## 关于内嵌：这是对早先决定的推翻

初版刻意**不**内嵌那两个面板，理由是「一个 tab 一个 panel」的 IA 和 lazy chunk
成本。Owner 要求在 studio 里就能配完，所以推翻。**复用而不是重新实现**：自己再
写一份渠道状态读取或 skill 列表，那两个 tab 一改就会漂移。代价是这个 tab 会连带
拉它们的 chunk —— 已接受。

## 建议与真实配置是分开的两层

对话产出的**建议**（skill / channel）压在各自 section 上方，装与绑仍然要人点。
理由见 [[builderApply.ts]]：装 skill 会往 workspace 复制文件，模型改主意就会当着
用户的面装了又卸；绑渠道要凭证，凭证从用户直达后端，绝不进对话信封。

结构由 `components/bookmarks/__tests__/builderTab.test.tsx` 钉住，包括「没有头像、
没有描述字段」这两条否定断言。

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
