---
code_file: frontend/src/pages/CreateAgentPage.tsx
last_verified: 2026-08-24
stub: false
---

## 2026-08-24 (14) — 改成 2 步向导：Step 1 选/连 Provider，Step 2 才是原来那 4 个框

设计文档：`reference/self_notebook/specs/2026-08-24-create-agent-provider-wizard-design.md`。

新增 `WizardStep = 'provider' | 'details'`（`step` state，默认 `'provider'`），
`DialogFooter` 按 `step` 切两套按钮——Step 1 是 Cancel / Next（`onClick={() =>
setStep('details')}`，不做任何前置校验，Provider 是可选的），Step 2 是 Back
（回 Step 1）/ Cancel / Create。

**Step 1（provider）**——把"选一个已有 provider，或添加一个新的"整体前置到
Awareness 框之前，而不是像原来那样只在 Engine 框里用两个原生 `<select>`
（Framework + Provider）隐式表达：

- 已有 provider 列成一排可点行（`bindableProviders` = `providerList` 按
  `netmindOnly`/`isSlotBindableSource` 过滤，跟 Engine 框原来的过滤逻辑一
  致），点击 `selectProvider(pid)` → `applyProviderSelection` 用新增的
  `deriveFrameworkFromProvider(prov)`（[[../../lib/agentFramework]]）算出这个
  provider 对应哪个 `framework`（`claude_code`/`codex_cli`/…），同时用
  `getModelsForSlot(prov, 'agent', fw, {})` 取第一个模型预填 `agentDraft.model`。
- "添加新 provider" 分两个方法按钮：**API key**（`addMethod='apikey'`，展开
  后再分 quick/custom 两个子标签——quick 渲染既有的
  `OneKeyOnboard`，custom 渲染本计划 Task 3 抽出的
  [[../../components/providers/CustomEndpointForm]]）和 **CLI 登录**
  （`addMethod='cli'`，渲染本计划 Task 4 抽出的
  [[../../components/providers/CliSignInPanel]]，`netmindOnly` 时整个按钮隐
  藏——云端非 staff 用户没有 CLI 登录路径,跟 Engine 框旧版
  `frameworkSwitchBlocked` 那条云端门禁是同一条规则)。三个组件共享的网络层
  是 [[../../components/providers/providerApi]]。
- 新 provider 添加成功后统一走 `handleProviderAdded`：重新
  `api.getProviders()`,用"添加前后 id 集合差集"找出新 provider,自动
  `applyProviderSelection` 选中它并收起 `addMethod` 面板——用户不用添加完还
  要再手动点一次刚加的那一行。

**Step 2（details）**——还是原来 (0)~(13) 条目描述的 Awareness / Engine /
Channel / Config 四个框,但 Engine 框头部变了：原来 Framework + Provider 两
个独立的 `IconSelect` 下拉被替换成一行**只读摘要**（provider 名 + 对应品牌
icon + `AGENT_FRAMEWORKS.find(f => f.id === framework)?.label`）+ 一个
"Change provider"链接（点击 `setStep('provider')`,退回 Step 1 重新选)。
Framework 不再是用户能独立掰动的旋钮——它完全从 Step 1 选中的 provider 派
生,用户没有办法选了 provider A 却手动把 framework 掰成不匹配的那个。Engine
框剩下的部分不变：Model（`IconSelect`,从选中 provider 的 `getModelsForSlot`
派生候选)、Thinking / Reasoning Effort 原生 `<select>`、以及完全独立的
Helper LLM provider+model 两个 `IconSelect`（helper 槽跟 Step 1 的 agent 槽
选择无关,继续保留自己的下拉,不受这次改动影响)。

`agentInitial`/`helperInitial`/`frameworkInitial` 三个"全局默认快照"和
`handleCreate` 里"只有偏离快照才写 per-agent override"的逻辑完全没变——
Step 1 选中一个跟全局默认一致的 provider 不会触发 override 写入,Create 时
不建 override 行,继续跟全局默认走。

## 2026-08-24 (13) — Description 字段换成 Instructions 大文本框

Owner 贴了截图，要求 Awareness 框里的 Description 从单行 `<input>`
换成截图那种多行、等宽字体、暗色圆角文本框（`rows` 高、placeholder
写引导性文字）。改法：`<input>` 换 `<textarea rows={6}>`，沿用
`inputCls` 这套边框/背景/文字颜色 token（跟其余控件视觉语言保持一致，
没有单独硬编码截图里那套纯黑背景 —— 截图是别处深色主题的样例，这个
弹窗本身是白底 `--nm-card` 三层配色，套截图的纯黑背景会破坏 (3) 定下的
"白壳 → 边栏黄框 → 白色控件"三层规则），额外加 `min-h-[140px]
resize-y py-2.5 font-mono leading-relaxed` 让高度可长、允许用户拖拽
放大、字体走等宽（跟 `fieldLabel` 已经在用的 `font-mono` 呼应，视觉
上更接近截图那种"代码编辑器"质感）。

`state`（`description`）和提交逻辑（`handleCreate` 里拼 awareness seed
的 `${name}\n\n${description}`）完全没变 —— 只是输入控件从单行换成
多行，字段语义（种子文案，不是强校验字段）不变。

i18n 层面把 `pages.createAgent.descLabel` / `descPlaceholder` 从
"Description" / "What does this agent help with?" 改成
"Instructions" / "Write what this agent should do, what to focus on,
what to avoid…"（10 locale 全部翻译过，非机器直译）—— label 换词是
因为新 placeholder 明确在教用户"写指令"而不是"写一句话描述"，
"Instructions" 更贴合截图原文，也避免跟 `createTeam.descLabel`
（团队描述，语义不同）的英文撞词造成混淆。

## 2026-08-20 (12) — IconSelect 下拉透明背景，根因在共享 Popover 组件

上面 (11) 修的 z-index 只解决了"挡在 Dialog 后面"，Owner 之后又反馈
"list 不应该透明背景"——列表其实已经在最上层了，但看起来跟背景内容
糊在一起。根因不在这个页面，在共享组件
[[../../components/ui/Dialog.tsx]] 的兄弟文件
[[../../components/ui/popover.tsx]]：`PopoverContent` 自己的
`style={{background: 'var(--nm-raised)', ...}}` 写在前面，`{...props}`
展开写在后面——`IconSelect` 传的
`style={{width: 'var(--radix-popover-trigger-width)'}}` 被 JSX 的对象
展开语义整个替换掉了原来那个 style 对象，不是合并，background/border/
shadow 全部消失，下拉列表变成完全透明。修法在 popover.tsx 那边：把
`style` 从 `props` 里解构出来，塞进基础 style 对象内部
（`{background: ..., ...style}`），而不是靠展开顺序隐式覆盖。这是个
共享组件级别的 bug，不是这个页面独有的——以后随便哪个调用方给
`PopoverContent` 传 `style` 都会踩到，不只是 IconSelect。

## 2026-08-20 (11) — 有色图标 + Popover 被 Dialog 挡住的 bug

Owner 反馈两件事：

1. **"这些 icon 可以是有色的"**：之前所有品牌图标（渠道 + 模型厂商）都用
   `fill="currentColor"`，跟随文字的墨色/灰色，Owner 觉得应该显示品牌
   真实颜色。改法见 [[../../components/icons/ChannelBrandIcons.tsx]] 和
   [[../../components/icons/ModelBrandIcons.tsx]] 各自的改动记录——
   每个图标现在都带自己的官方 hex。连带效果：Channel 图标行原来靠
   `text-[var(--nm-ink)]`/`text-[var(--nm-ink30)]` 切换展开/收起的深浅，
   现在图标颜色是硬编码的，文字颜色类完全不起作用了——统一改成
   `opacity-100 grayscale-0`（展开）/`opacity-40 grayscale`（收起），
   7 个渠道图标（含 Lark/NarraMessenger 两个 `<img>`）用同一套滤镜逻辑，
   删掉了原来专门给 `<img>` 开的 `IMG_BASED_CHANNELS` 特例判断——反正现在
   全部图标都是"固定色"，不用再分两类。

2. **"为什么现在不能拉开 list 了"**：Engine 框新增的 5 个 `IconSelect`
   下拉打不开（技术上其实是打开了，但看不见也点不到）。根因是
   `components/ui/popover.tsx` 的 `PopoverContent` 默认 `z-[100]`，而这个
   页面整个渲染在 [[../../components/ui/Dialog.tsx]] 里面——Dialog 的
   内容包裹层是 `z-[1001]`，Popover 又是单独 portal 到
   `document.body`（跟 Dialog 是兄弟节点，不是子节点），100 < 1001，
   下拉列表实际上画在了弹窗卡片**后面**。修复：本文件里两处
   `<PopoverContent>`（IconSelect 的 + Skills 选择器的）都加
   `z-[1100]` 覆盖默认值。这是个通用教训——**任何东西如果最终会被塞进
   这个 Dialog 里，用到 Popover/Dropdown/Tooltip 之类会 portal 的组件时
   都要检查 z-index 有没有盖过 1001**，不是 IconSelect 一个组件的孤立
   bug。

## 2026-08-20 (10) — Engine 框的 Framework/Provider/Model/Helper LLM 也换真图标

Owner 贴了截图指名要 Engine 框里的 Framework、Provider、Model、
Helper LLM 四个下拉都带真实 icon。**技术前提**：原生 `<select>` /
`<option>` 在任何浏览器里都不能在选项里塞图标——这是浏览器层面的硬限制，
不是样式问题。所以这四个（连同 Helper LLM 的 provider/model 一起算，
一共 5 处）全部从原生 `<select>` 换成新写的 `IconSelect`——本文件内一个
Popover 触发的自定义下拉组件（跟 Skills 选择器同一套交互：按钮触发器 +
`PopoverContent` 列表），触发器和列表项都能塞图标。`PopoverContent`
用 `style={{ width: 'var(--radix-popover-trigger-width)' }}` 让下拉宽度
跟触发器对齐（确认过 `@radix-ui/react-popover` 确实会自动设置这个
CSS 变量，不是瞎猜的）。Thinking / Reasoning effort 两个下拉没有品牌
概念（Auto/低/中/高这种词），维持原生 `<select>` 不变。

配图来源分两层：

- **Framework**（`AGENT_FRAMEWORKS` 的 3 个 id）：`FRAMEWORK_ICONS` 静态
  映射——`claude_code`→Claude、`codex_cli`→OpenAI、`nexus_power`→本 App
  自己的 logo（[[../../components/icons/ChannelBrandIcons.tsx]] 新增的
  `NexusPowerBrandIcon`，跟 NarraMessenger 共用同一个 `AppLogoIcon` 内部
  组件，只是 `alt` 不同）。
- **Provider**（agent 槽位和 helper 槽位共用）：按 `protocol` 走——
  `lib/agentFramework.ts` 里 `ProviderSummary.protocol` 只有
  `'anthropic' | 'openai'` 两个值（研究过一遍，全仓库没有第三个），所以
  `getProtocolBrandIcon(protocol)` 只需要两个分支，不用管 `source`
  （`netmind` / `netmind_free` / `yunwu` / `openrouter` /
  `claude_oauth` / `codex_oauth` 这些 8 种来源都不单独配图标——它们的
  品牌信息已经写在下拉文字里了，"NetMind (Anthropic)" 这种，图标只需要
  传达"底层是 Claude 还是 GPT"）。
- **Model**（agent 槽位和 helper 槽位共用）：`getModelBrandIcon(modelId)`
  按 model_id 里的关键字子串匹配（不区分大小写）——覆盖
  `MODEL_SUGGESTION_GROUPS` 里的全部 8 个厂商：Claude/OpenAI/Gemini/
  GLM(Zhipu)/Kimi(Moonshot)/Qwen/MiniMax/DeepSeek。自定义 base_url
  provider 可能带任意字符串的 model_id，匹配不上就返回 `null`——
  `IconSelect` 对 `Icon: null` 的选项直接不渲染图标位，不会留空洞或报错。

`getProtocolBrandIcon` / `getModelBrandIcon` 两个纯函数没有放进
`ModelBrandIcons.tsx`，单独抽到新文件
[[../../lib/modelBrandIcons.ts]]——react-refresh 的 lint 规则不允许一个
文件里混合"导出 React 组件"和"导出普通函数"，混了会跑两条
`react-refresh/only-export-components` 报错（在 `ChannelBrandIcons.tsx`
那批 Channel 相关改动时没踩到这个坑，是因为那边所有导出都是组件；这次
新增两个匹配函数才第一次撞上）。

顺手把 `fieldLabel` / `inputCls` / `selectCls` / `boxCls` / `boxTitleCls`
这几个样式字符串从组件内部的 `const` 提到模块顶层——`IconSelect` 作为
文件内的兄弟组件也要用同一套视觉语言，之前它们是每次渲染都重新拼一遍
的组件内局部变量，提到模块级别顺带也省了这份重复分配。

## 2026-08-20 (9) — Lark 常亮 bug 修复 + 第 7 个渠道 NarraMessenger

**Bug**：Owner 发现 Lark 图标"默认是亮的"。根因是 (7) 里给 Lark 的
`<img>` 没套任何 dim 处理——`key !== 'lark' && (expanded ? ink : gray)`
这行直接跳过了 lark，导致它的真彩位图永远满色显示，跟其余五个"未展开
即灰"的矢量图标放一起，视觉上像是常驻高亮，跟绿点（真正的"已 Connect"
状态）是两回事，容易看错。修复：改成
`IMG_BASED_CHANNELS.has(key) ? (expanded ? 'opacity-100 grayscale-0' :
'opacity-40 grayscale') : (currentColor 那套)`，位图现在展开时满色、
收起时 40% 透明度 + 灰阶滤镜，跟矢量图标的"收起灰、展开深"节奏对齐。

**新增 NarraMessenger**：Owner 追问漏了 `IMChannelsSection.tsx` 里
`IM_CHANNELS` 数组的第 7 个条目（第 (5)/(6) 轮范围只覆盖了 Owner 最初
点名的 6 个）。字段只有一个——`bindCommand`（从 NarraMessenger App 的
"我的空间→我的智能体→绑定智能体"复制的一次性绑定链接），对应
`NarramessengerConfig.tsx` 的 `handleBind` →
`api.bindNarramessenger(agentId, bindCommand)`。这个真实组件**完全没有
i18n**（硬编码英文字符串，零 `t()` 调用），所以字段 label/placeholder
是新加的 `pages.createAgent.narramessengerBindLabel` /
`narramessengerBindPlaceholder`（10 locale 都补了），Connect 按钮复用
`awareness.common.bindBot`。图标是 [[../../components/icons/ChannelBrandIcons.tsx]]
新增的 `NarraMessengerBrandIcon`——NarraMessenger 是本产品自己的伴生
App，不是第三方平台，它的"真实 icon"直接就是本 App 自己的 logo
（跟 `IMG_BASED_CHANNELS` 一样走灰阶/透明度 dim，不走 currentColor）。
跟其余渠道一样，走 Connect 确认 → `connectedChannels` → Create 时才
发 `bindNarramessenger` 的同一套流程，不是特例。

## 2026-08-20 (8) — Channel 改手风琴 + 显式 Connect 才点亮

Owner 反馈两点，都在改"什么时候算这个渠道配置好了"：

1. **单开手风琴，不再累加**：`expandedChannels: Set<ChannelKey>` 改成
   `expandedChannel: ChannelKey | null`。点一个图标展开它，点另一个直接
   切过去（不是先收起当前那个再展开新的两步操作，`toggleChannel` 就是
   `prev === key ? null : key`），之前点开的表单区域消失但**草稿 state
   不清空**（`discordDraft` 等仍是独立 state，隐藏不等于丢弃，回头点开
   还在）。
2. **图标点亮从"自动检测字段非空"改成"显式点 Connect"**：(7) 里的
   `isChannelConfigured` 是纯派生值，输入框一有字符绿点就亮，Owner 觉得
   这样不对——改成新 state `connectedChannels: Set<ChannelKey>`，只有点了
   表单底部的 Connect 按钮（Discord/Telegram/Slack/Lark 用
   `awareness.common.bindBot`"Bind Bot"，Home Assistant 用
   `awareness.common.save`"Save"——直接搬真实组件自己的按钮文案，不新造
   "Connect"这个词）才会把 key 塞进 `connectedChannels`，同时自动收起该
   面板（`setExpandedChannel(null)`）。校验逻辑单独抽成
   `channelHasRequiredFields(key)`（原来的 `isChannelConfigured` 改名），
   Connect 按钮点击时校验不过就设 `channelError`
   （`pages.createAgent.channelConnectMissing`，10 locale 都补了）并不
   收起、不点亮，通过才收起+点亮。

   **这个 Connect 按钮不打任何 API**——每个渠道的真实 bind 接口都要求
   已存在的 `agent_id`，创建页这时候还没有，点击 Connect 纯粹是本地状态
   确认（"我认这份草稿"），真正的 `bindDiscordBot` /
   `bindTelegramBot` / `bindSlackBot` / `bindLarkBot` /
   `saveHABinding` 调用仍然在 `handleCreate` 里、agent 建好之后才发生。
   `handleCreate` 里原来"字段非空就发请求"的判断加了一层
   `connectedChannels.has(key) &&` 前置——没点 Connect 的草稿即使填了
   字段，Create 时也不会被绑定，跟 Skills 未勾选不装是一个道理。

   WeChat 展开区没有 Connect 按钮（它压根没有可填字段，`
   channelHasRequiredFields('wechat')` 恒 `false`），只显示占位说明，
   永远点不亮，这是预期行为。

## 2026-08-20 (7) — Lark 也换真图标；卡片变大 + "已配置"状态点

Owner 追问"全都得换成真图标"（(6) 里 Lark/Feishu 还留着通用
`MessageSquare`）。Simple Icons 官方目录确认没有 Lark/Feishu 条目，又用
Iconify 的全库搜索（覆盖上百个图标集）交叉确认了一遍——`lark` 只搜出
一堆"云雀鸟"图标和不相关的 "olark" 客服组件，`bytedance` 只有母公司
自己的 logo，都不是 Lark/Feishu 这个 IM 产品的标志。最后从
`larksuite.com/favicon.ico` 直接拿的官方 favicon（本人网站的真实资产，
不是描摹），`magick` 转成 96×96 PNG 存进
`frontend/public/channel-logos/lark.png`（走 public/ 根路径引用，跟
`Sidebar.tsx` 的 `/logo-dark-mode.svg` 同一约定，不是 JS import）。
[[../../components/icons/ChannelBrandIcons.tsx]] 新增 `LarkBrandIcon`，
`<img>` 渲染，是六个里唯一保留真实品牌色（不跟随 `currentColor`）的
一个——其余五个是矢量单色路径，Lark 只有这张位图。

同时按 Owner 要求把图标行从 40×40 的纯图标格子换成 76px 宽的卡片
（图标 24px + 下方 10px 渠道名），并加了"已配置"状态点：右上角一个绿色
小圆点，出现条件是 `isChannelConfigured(key)`——直接复用 `handleCreate`
里判断"这个渠道要不要发 bind 请求"的同一套必填字段检查（Discord 看
botToken、Slack 看 botToken+appToken、Lark 看 appId+appSecret、HA 看
baseUrl+token），保证状态点和实际提交行为不会对不上。WeChat 恒为
`false`——它的绑定是活会话，创建页永远填不出"已配置"这个状态，这是
预期行为，不是漏做。新 i18n key 只加了一个：
`pages.createAgent.channelConfigured`（状态点的 tooltip），10 个
locale 都补了。

## 2026-08-20 (6) — Channel 从禁用占位升级成真实内联表单

Owner 反馈"用真实 icon 而且点击后可以在下方延长出来配置"，把 (5) 里
刚做的"6 图标禁用占位"升级成两件事：

1. **真实图标**：新增 [[../../components/icons/ChannelBrandIcons.tsx]]
   （Discord/WeChat/Slack/Telegram/Home Assistant 的官方单色路径，
   项目里此前没有任何品牌 icon 资产，从 Simple Icons CDN 现抓，没引入
   新依赖）。Lark/Feishu 在 Simple Icons 里没有条目，继续用
   `lucide-react` 的 `MessageSquare` 兜底，不冒充。
2. **点击展开真表单，创建时同步提交**（模式确认："同 Skills 模式"，
   而不是"只预览不收集数据"那个选项）：
   - 图标从 `<div disabled>` 换成 `<button onClick={toggleChannel}>`，
     `expandedChannels: Set<ChannelKey>` 记录哪些展开了（互不排斥，可
     同时开多个）。
   - Discord / Telegram / Slack / Lark（token 模式）/ Home Assistant
     各自的字段**完全对齐真实组件**（`DiscordConfig.tsx` /
     `TelegramConfig.tsx` / `SlackConfig.tsx` / `LarkConfig.tsx` /
     `HomeAssistantConfig.tsx`）收集的参数，本地存草稿 state
     （`discordDraft`/`telegramDraft`/`slackDraft`/`larkDraft`/
     `haDraft`），点击 Create 后用新 `agent_id` 逐个调对应 bind
     接口——`api.bindDiscordBot` / `api.bindTelegramBot` /
     `api.bindSlackBot` / `api.bindLarkBot` / `api.saveHABinding`，跟
     Awareness/Engine/Skills 同一批 best-effort 失败收集逻辑（失败推
     进 `failures` 数组，不回滚 agent）。哪个 token 字段没填就跳过
     那个渠道，不强制全填。
   - 字段的 label/placeholder **全部复用** `awareness.discord.*` /
     `awareness.telegram.*` / `awareness.slack.*` / `awareness.lark.*` /
     `awareness.homeAssistant.*` 已有的 10-locale 翻译（真实渠道组件
     已经用了这些 key），零新增 i18n key——唯一新东西是复用
     `discord.botToken`（"Bot Token"）当 Discord/Telegram/Slack 三处
     通用的机器人 token 字段标签，因为原文案本来就是通用词，没有
     Discord 专属语义。
   - **WeChat 是唯一没有真表单的**：它的绑定是活会话
     （`startWeChatQrcode(agentId)` 必须拿到真实 agent_id 才能生成二维
     码），根本没有"可以预填的字段"，所以 WeChat 展开区仍然只显示
     `channelPlaceholderHint`（"创建后可配置"），跟 Lark 的 OAuth 设备
     码登录子流程一样——只有 Lark 的 token-bind 表单模式被搬进来了，
     OAuth 登录模式没有，因为那也要活 agent_id 才能发起。

## 2026-08-20 (5) — Engine 之后加 Channel 框（同 MCP，禁用占位）

Owner 要求在 Engine 下面加一个 Channel 框，"可以配置各种渠道"。研究
（见下）确认现有 6 个渠道配置——[[../../components/awareness/WeChatConfig.tsx]] /
[[../../components/awareness/DiscordConfig.tsx]] /
[[../../components/awareness/SlackConfig.tsx]] /
[[../../components/awareness/TelegramConfig.tsx]] /
[[../../components/awareness/LarkConfig.tsx]] /
[[../../components/awareness/HomeAssistantConfig.tsx]]——**全部**从
`useConfigStore()` 自己读 `agentId`，每个 bind/QR/OAuth 调用都把
`agent_id` 塞进请求体或路径，没有任何一个能在 agent 不存在时跑：
WeChat 的 `startWeChatQrcode(agentId)` 连开始扫码会话都要活的
`agent_id`；Lark 的 OAuth device-code 登录 `larkAuthLogin(agentId)` 同理。
这跟 MCP 当初被砍成占位的原因**完全一样**（见下面"MCP 为什么是禁用
占位"），所以 Channel 框也做成同款禁用占位——不是新引入的判断，是同一条
"没有 agent-independent 目录/绑定入口"规则第二次命中。

UI 上跟纯文字占位（MCP）不一样的地方：Owner 要求列出 6 个具体渠道
图标（灰色、不可点）而不是一行字，图标复用
[[../../components/awareness/IMChannelsSection.tsx]] 里 `IM_CHANNELS`
数组同款的 lucide 图标映射（Lark→MessageSquare, Slack→Hash,
Telegram→Send, WeChat→QrCode, Discord→Bot），Home Assistant 不在那个
数组里（`AwarenessPanel.tsx` 里单独渲染），图标用它自己的 Home。每个
图标的 `title` tooltip 是 `"{渠道名} · {创建后可配置}"`，渠道名跟
`IMChannelsSection` 一样写死英文品牌名，不进 i18n；只有共享的
"创建后可配置" 后缀（`channelPlaceholderHint`）和框标题
（`channelSectionTitle` + `optionalSuffix`，跟 Config 框一样标 optional）
进了全部 10 个 locale。

真正渲染这 6 个渠道配置组件的地方——`IMChannelsSection.tsx` 挂在
`AwarenessPanel.tsx:303`，`AwarenessPanel` 又挂在
`BookmarkPanelHost.tsx` 的 `channels` 标签页——完全没动，创建页只是
"提前预告有这些渠道"，真正配置还是走 agent 建好之后的书签抽屉。

## 2026-08-20 (4) — Awareness/Engine 标题加 *，Config 标记 optional

Owner 反馈：Awareness / Engine 两个框标题后面加 `*`（纯标点，跟 Name
字段的 `* ` 前缀一样直接写死在 JSX 里，不进 i18n），Config 标题后面加
新 key `pages.createAgent.optionalSuffix`（"(optional)"/"（可选）"…，
10 个 locale 都加了）。注意这只是标题旁的视觉提示，不代表校验规则变了
—— `handleCreate` 仍然只硬校验 `name`，Engine 的 provider/model 依旧是
"选了就写 override，不选就继续继承全局默认"，不会因为标题带 `*` 就在
提交时强制要求填。

## 2026-08-20 (3) — 配色再调一轮：外壳白、框黄

Owner 二次反馈"底用白的，框用微黄——左侧边栏的黄"：Dialog 外壳从默认
`var(--nm-raised)` 改传 `bg="var(--nm-card)"`（纯白，新加的 Dialog prop
见 [[../../components/ui/Dialog.tsx]]）；三个框（`boxCls`）从白色改回
`var(--nm-paper)`（左侧边栏自己的底色）；框里的 input/select
（`inputCls`/`selectCls`）从 `--nm-paper` 改回白色 `--nm-card`。最终
三层是：白色弹窗壳 → 边栏同色框 → 白色表单控件——跟上一轮（暖色壳 →
白框 → 边栏色控件）正好颜色互换了一层，颜色语义稳定在"边栏黄只出现在
框本身，壳和控件都是白的"。

## 2026-08-20 (2) — 从整页改成弹窗（Dialog 复用）

Owner 反馈想试试"表格弹窗外部置灰"，于是把原来的整页布局（自建
top bar + ScrollArea + 底部 sticky 操作条）换成直接用共享
`Dialog`/`DialogContent`/`DialogFooter`（`components/ui/Dialog.tsx`）：

- `<Dialog isOpen onClose={() => navigate('/app/chat')} title={...}
  size="3xl">` —— `isOpen` 恒真（这个组件只在路由匹配时才挂载，不需要
  额外开关状态）；`onClose` 复用跟 Cancel 按钮一样的 `navigate`。
  Escape / 点击背景遮罩 / 头部 X 都会走同一个 `onClose`，不用各自接线。
- 三个框搬进 `DialogContent`，Cancel/Create 搬进 `DialogFooter`——不再
  需要自己管 sticky 底栏或者外层滚动（Dialog 自带
  `fixed inset-0 overflow-y-auto`）。
- 路由 `/app/agents/new` 没变，仍走 [[../../App.tsx]] 的懒加载 Route；
  只是这个组件现在 return 的是一个 portal 到 `document.body` 的 Dialog，
  而不是撑满 `<main>` 的整页内容——`<main>` 本身在这个路由下基本是空的，
  Dialog 的暖调半透明背景（`var(--nm-backdrop)` + `blur(2px)`）把它后面
  的侧栏/顶栏（不含 chat——`isSubPage` 路由本来就不挂载 ChatView）压暗，
  这就是"外部置灰"的来源。

配色跟着换了一轮：Dialog 外壳是它自带的 `var(--nm-raised)`（暖调）；三
个框（`boxCls`）改回纯白 `--nm-card`，在暖色壳子上当白卡片浮出来；框
里的 input/select（`inputCls`/`selectCls`）改用 `var(--nm-paper)`——
就是左侧边栏自己的底色（Owner 明确要这个"黄"，不是更饱和的
`--nm-paper-warm`）——所以视觉上是：暖色弹窗壳 → 白色卡片 → 边栏同色
表单控件，三层。

`* 返回` 按钮删掉了（Dialog 头部自带标题 + X，不需要重复的返回箭头），
对应 `pages.createAgent.back` i18n key 也从全部 10 个 locale 删除。

# CreateAgentPage — v4 Agent 创建弹窗

## 为什么存在

原来侧栏 New 菜单点「创建 Agent」是 `useCreateAgent()` 一键建空白
Agent，名称/描述/框架/模型/技能都要建完之后再逐个面板补。这个页面
把它换成参考截图同款的表单，三个框：

- **Awareness**：名称 + 描述
- **Engine**：框架 + Agent 槽位（provider/model/thinking/reasoning_effort）
  + Helper LLM 槽位（provider/model）
- **Config**：技能多选 + MCP（禁用占位）

## 后端没有原子创建接口 —— 前端依次调用

`POST /agents`（`api.createAgent`）只收 `agent_name` /
`agent_description` / `team_id`，不认框架/模型/技能/MCP。Engine 和
Skills 都要求已存在的 `agent_id`（分别是
`PUT /agents/{id}/llm-config/{slot}` 和
marketplace `POST /skills/{id}/install`）。所以 Create 点击后的顺序是：

1. `useCreateAgent().createAgent({name, description})` 建 agent，拿到
   `agent_id`（这一步失败就整体中止，什么都不建）。
2. 用 `agent_id` 并发/顺序补：`api.updateAwareness`（种子内容 =
   `"${name}\n\n${description}"`）、`setAgentLlmConfig('agent', …)`（仅
   当用户真的选了 provider+model 才写，没碰的槽位继续继承全局默认）、
   `setAgentLlmConfig('helper_llm', …)`（同理）、以及每个勾选技能一次
   `installMarketplaceSkill`。
3. 第 2 步里任何一个失败都不回滚、不删 agent —— 用失败项拼一条
   `useNotice().notifyError` 提示，然后照常 `navigate('/app/chat')`。
   每个配置项本来就有自己的补救入口（AgentLlmConfigPanel /
   AwarenessPanel / SkillsPanel），没必要在这里做事务语义。

## MCP 为什么是禁用占位

`backend/routes/agents/mcps.py` 下所有 MCP 接口都要求已存在的
`agent_id`，后端没有全局/模板目录。唯一能在建页前读到东西的是
`POST /bundle/export/preview/mcps`，但那是给 Bundle 导出向导用的（读
调用者名下其他 agent 的 MCP 列表），语义对不上"新建时选 MCP"，硬套
会把无关功能的接口拉进创建流程。所以这里 MCP 行只显示"创建后可配置"
占位文字，不可点击。

## Engine 字段来源 + 默认预选

Provider 列表 / 默认框架取 `api.getProviders()` +
`api.getAgentFramework()`（跟 `ModelDefaultsSettings.tsx` 读全局默认
用的是同一对接口，agent 还不存在时没法用 `AgentLlmConfigPanel` 那套
`getAgentLlmConfig(agentId)`）。字段渲染、可选 provider/model 过滤逻辑
直接复用 `lib/agentFramework.ts` 的共享 helper，跟
`ModelDefaultsSettings` / `AgentLlmConfigPanel` 三处保持完全一致的选项
集合。

Agent 槽位 / Helper 槽位跟 framework 一样，用 `getProviders()` 返回的
`data.slots.agent.config` / `.helper_llm.config` 预填（Owner 反馈：截图
那种空下拉不该出现，应该跟 Settings › 模型默认值一样带出当前全局
默认）。预填值同时存进 `agentInitial` / `helperInitial` /
`frameworkInitial` 三个快照 —— Create 时只有真正偏离快照的槽位才会写
`setAgentLlmConfig` per-agent override；原样未动的槽位不建 override
行，继续跟全局默认走，不会把"今天的全局默认"钉死成这个 agent 的永久
覆盖值。

四处说明性小字（Awareness / Engine 两个 section 副标题
awarenessSectionHint / engineSectionHint，以及 Skills / MCP 行下方的
skillsManageHint / mcpHint）按 Owner 反馈整体去掉了，对应 i18n key 也
从全部 10 个 locale 里删除 —— 不留没人读的死 key。三个框现在只剩标题 +
控件，不带解释性文案。

## 技能选择器

`useMarketplaceSearch(q, enabled)` 的 `agentId` 是可选参数——不传时
返回匿名/全局市场列表，因此可以在 agent 还不存在时用来做预选。选择器
是自建的 Popover + checkbox 列表（`@/components/ui/popover`），仿
`CreateTeamPage.tsx` 的 members 清单交互（勾选进本地 Map，不在 Popover
关闭前就调用任何 API）。安装动作全部推迟到 Create 点击之后逐个调
`api.installMarketplaceSkill(skillId, agentId)`，不经过
`useMarketplaceInstall()`（那个 hook 从 `configStore.agentId` 读目标
agent，建页阶段还没有这个值，直接传参更明确）。

## MainLayout 的浮动 X 被跳过

[[../../components/layout/MainLayout.tsx]] 给每个 sub-page 路由无条件
加一个 top-4 right-4 的浮动 X（回到聊天）。`hasOwnCloseControl`（按
pathname 匹配 `/app/agents/new`）跳过它——现在这个页面本身是个 Dialog，
Dialog 自己头部就有 X，两个 X 会重复。这个开关目前实际上是"防御性"的：
Dialog 是 `createPortal` 到 `document.body`，`<main>` 那个浮动 X 就算
不跳过也会被 Dialog 的 `z-[1000]` 背景压在下面看不见，但保留判断更清楚
地表达"这个路由自己管关闭"。

## 入口改线

`components/layout/Sidebar.tsx` 的 `CreateMenu.onCreateAgent` 从
`() => void createAgent()` 改成 `() => navigate('/app/agents/new')`；
`AgentList.tsx` 和 `OnboardingChecklist.tsx` 里各自的"快速建空白 Agent"
按钮不受影响，仍走 `useCreateAgent()` 原来的一键路径 —— 只有侧栏 New
菜单这一个入口换成这个表单。`useCreateAgent` 本身只是把
`opts.name`/`opts.description` 透传给 `api.createAgent`（原来两个参数
一直被硬编码成 `undefined`），三个调用方共享同一份 store 接线 + 上线
里程碑逻辑不变。
