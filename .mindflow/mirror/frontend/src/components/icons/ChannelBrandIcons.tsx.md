---
code_file: frontend/src/components/icons/ChannelBrandIcons.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 (4) — 从 currentColor 改成品牌真彩

Owner 反馈"这些 icon 可以是有色的"。原来 `BrandIcon` 固定
`fill="currentColor"`，所有矢量图标（Discord/WeChat/Slack/Telegram/
HomeAssistant）跟随文字墨色，改成 `BrandIcon` 新增必填 `color` prop，
每个导出函数传自己的官方 hex：Discord `#5865F2`、WeChat `#07C160`、
Telegram `#26A5E4`、HomeAssistant `#18BCF2` 都是 Simple Icons 注册表里
现成的值；Slack `#4A154B`（Aubergine）比较特殊——**OpenAI 和 Slack 这两个
品牌当前已经不在 Simple Icons 的 metadata 注册表里**（`data/
simple-icons.json` 里搜不到，猜测是被品牌方要求下架，但 jsdelivr CDN
还在返回旧版本的 SVG 文件，属于缓存滞后，不代表官方还认可这个用法），
所以 Slack 用的是官方 brand style guide 公开的 Aubergine 色号，不是从
Simple Icons 数据里查到的。

连带修了一个"图标常驻高亮"的分支：Channel 图标行原来靠 CSS 文字颜色
（`text-[var(--nm-ink)]`/`text-[var(--nm-ink30)]`）表达展开/收起状态，
矢量图标改用固定 hex 后这套颜色切换直接失效——调用方
[[../../pages/CreateAgentPage.tsx]] 统一换成 opacity/grayscale 滤镜，
删掉了原来只给 `<img>` 图标（Lark/NarraMessenger）开的特例判断。

## 2026-08-20 (3) — NexusPower 复用 NarraMessenger 的 AppLogoIcon

[[../../pages/CreateAgentPage.tsx]] Engine 框的 Framework 下拉需要给
`nexus_power`（本 App 自己的引擎，见 `AGENT_FRAMEWORKS`）配真图标——
跟 NarraMessenger 一样是第一方产品，没有独立设计的品牌资产，直接用
App 自己的 logo。把 `NarraMessengerBrandIcon` 里内联的
"读 `useTheme()` 切 dark/light logo 路径"逻辑抽成私有的
`AppLogoIcon({className, alt})`，`NarraMessengerBrandIcon` 和新增的
`NexusPowerBrandIcon` 都只是传不同 `alt` 调用它——两个真实场景公用同一份
渲染逻辑，不是提前抽象。

## 2026-08-20 (2) — 第 7 个：NarraMessenger + Lark "常亮" bug 的教训

新增 `NarraMessengerBrandIcon`——跟 `LarkBrandIcon` 一样是 `<img>`，但
来源完全不同：NarraMessenger 是本产品自己的伴生 App（不是要去外面找的
第三方品牌），它的"真实 icon"就是这个仓库自己已有的
`/logo-dark-mode.svg` / `/logo-light-mode.svg`（`Sidebar.tsx` /
`SetupPage.tsx` 同款），组件内部用 `useTheme()` 的 `isDark` 切换深浅色
版本——这是本文件唯一会用 hook 的图标组件，其余都是纯函数。

**顺带修的教训**：`<img>` 图标（Lark、现在加上 NarraMessenger）不跟随
`fill="currentColor"`，调用方 [[../../pages/CreateAgentPage.tsx]] 之前
只用 `key !== 'lark'` 做了个特例判断，加了 NarraMessenger 之后如果继续
一个个加特例迟早漏（事实上 Lark 那次就漏了 dim 处理，导致图标显示成
"常亮"）。所以调用方那边改成维护一个
`IMG_BASED_CHANNELS: ReadonlySet<ChannelKey>`（目前 `lark` +
`narramessenger`），而不是散落的 `key === 'xxx'` 判断——以后再加一个
`<img>` 图标，改一处集合定义就行。

# ChannelBrandIcons.tsx — real IM-channel brand marks

## 为什么存在

[[../../pages/CreateAgentPage.tsx]] 的 Channel 框最初用的是
`IMChannelsSection.tsx` 同款的 lucide 通用图标（Discord→Bot、
Slack→Hash 之类的占位符）；Owner 反馈要"真实 icon"。项目里没有任何
现成的品牌 logo 资产或图标库（`package.json` 只有 `lucide-react`，
`grep` 遍历 `public/`、`src/assets/` 也没有品牌 svg/png），所以这里从
Simple Icons（CC0 协议，`cdn.jsdelivr.net/npm/simple-icons@latest/
icons/<slug>.svg`）现抓的官方单色路径，本地建组件，不引入新依赖。

## 覆盖范围

七个全有真实品牌图标。Discord / WeChat / Slack / Telegram /
Home Assistant 是 Simple Icons 官方 monochrome 矢量路径。

**Lark/Feishu 是例外**：Simple Icons 目录里没有 Lark 或 Feishu 的条目
（`lark` / `feishu` / `larksuite` 都 404，索引里只有母公司
"ByteDance" 一条，不是同一个标志），又用 Iconify 的跨图标集搜索
（`api.iconify.design/search?query=lark`，覆盖上百个开源图标集）确认了
一遍——搜出来的全是"云雀鸟"图标或不相关的 "olark" 客服组件产品，没有
一个是 Lark/Feishu 这个 IM 应用的标志。矢量路径确实找不到，于是改用
`https://www.larksuite.com/favicon.ico`（官方网站自己的资产，权威来源，
不是第三方描摹）、`magick` 转成 96×96 PNG，存进
`frontend/public/channel-logos/lark.png`。`LarkBrandIcon` 因此是这个
文件里唯一一个 `<img>` 组件而不是 `<svg><path>`，也是唯一保留真实品牌色
的——`fill="currentColor"` 那套矢量单色处理对位图不适用，调用方
（[[../../pages/CreateAgentPage.tsx]]）渲染它时不套灰阶/ink 颜色切换，
其余五个才会跟随展开/配置状态变灰或变深。

## 设计决策

- `fill="currentColor"`，不带任何品牌色——这几个图标目前只用在
  Channel 框里当灰色小图标（未激活态 `text-[var(--nm-ink30)]`），不是
  彩色 logo 展示场景，所以特意保持单色可继承。
- 每个导出组件是纯 `<svg><path d="..."/></svg>`，`viewBox="0 0 24 24"`
  跟 lucide 的网格一致，`className`/其余 SVG props 直接透传，调用方用
  法（`<DiscordBrandIcon className="h-4 w-4" />`）跟用 lucide 图标完全
  一样，可以互换。
- Path 数据是从 CDN 现抓的官方文件，没有做任何简化/近似——精确度比
  手画更重要，品牌标志画错比不放更糟。
