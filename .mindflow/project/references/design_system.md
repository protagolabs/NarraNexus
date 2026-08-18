# NarraNexus 前端设计系统(Design System)

> **When to read**:写任何前端 UI(新组件、新页面、改样式)之前;做 UI code review 时;
> 讨论"这里该用哪个组件/哪档圆角/哪个颜色"时。
>
> **本文档的性质**:成文化**现有**的 "Nordic archive" 设计语言,不引入新视觉。
> 所有规范值从 2026-08-11 对 `feat/chat-ui-v4` 分支的全量审计中提炼
> (数据见附录),原则是**从现状主流用法里定标准,把少数派归拢**——
> 文档本身零像素变化;按文档收敛存量代码是独立的后续 PR。
>
> 维护:改 `index.css` 的 token 定义、或新增/裁撤设计系统组件时,同一 commit 更新本文。

---

## 0. 一页速查(TL;DR)

| 要做的事 | 规范 |
|---|---|
| 用颜色 | 只用**语义层** token(§2),禁止 `--color-red-500` 直连、禁止 hex |
| 中性底色 | 按 §2.5 四层表面阶梯取层;一屏 ≤4 层;同一功能件跨页面同层 |
| 圆角 | 只用 `var(--radius-*)`(§3),禁止 Tailwind `rounded-sm/md/lg/xl/2xl`;正圆用 `rounded-full` |
| 嵌套框圆角 | **内层圆角 = 外层圆角 − 间距**,内层永远 ≤ 外层(§3.2) |
| 字号 | 按 §4 阶梯取档,不再发明 `text-[12.5px]` 这类中间值 |
| 图标 | 只用 lucide-react(线性),尺寸三档 `h-3 / h-3.5 / h-4`,禁与实心图标混排(§5) |
| 选组件 | 查 §6 决策表;新界面**优先 `components/nm/`**,`ui/` 只用于表中标注的存留组件 |
| 新组件 API | shadcn 式约定:`variant` / `size` props + `cn()` 合并 + 组合优先(§7) |
| 明暗主题 | 永远经 token,token 自动切换;hex/调色板直连在暗色下必然出错(§8) |

---

## 1. 设计语言:Nordic archive

整个前端是一种"北欧档案馆"语言:纸(paper)与墨(ink)的中性底色、
极细的发丝线(hairline)分割、**克制的小圆角**(最大 12px)、
DM Mono 大写宽字距的小标签、少量语义色点缀。它刻意**不是**
圆润的 SaaS 风(大圆角、彩色渐变、重阴影)。

三个身份色贯穿所有"人/agent"语境,只此三个:

| token | 值 | 语义 |
|---|---|---|
| `--color-carbon` | `#E8704A` | 碳基 = 人类(用户) |
| `--color-silicon` | `#3D7EC4` | 硅基 = agent |
| `--color-overlap` | `#8E5CB8` | 人机重叠(混合场景) |

每个身份色配套 `-soft`(浅底)与 `-hair`(描边)变体。用户气泡右缘 carbon
条、agent 气泡左缘 silicon 条,是这个语言里最固定的语法。

---

## 2. 颜色 token:三层结构,只准用上两层

`index.css` 里的颜色分三层,**依赖方向只能向下**:

```
① 语义表面层  --nm-paper / --nm-ink / --nm-card / --nm-hairline / --nm-elev-* …
   (以及等价别名:--text-primary/secondary/tertiary、--bg-*、--border-*、--rule)
② 语义状态层  --color-success / --color-warning / --color-error / --color-info
              --color-carbon / --color-silicon / --color-overlap (+ -soft/-hair)
③ 调色板原语  --color-gray-50…950 / --color-red-* / --color-yellow-* / --color-green-* / --color-blue-*
```

**规则**:
- 组件代码(tsx / className / style)只允许出现 ①、② 两层。
- ③ 层只供 `index.css` 内部定义 ①② 时引用。`--color-red-500` 表达不了
  "这是错误色"的意图,暗色主题重定义时也只有语义层能整体翻转。
- **hex 颜色值在 tsx 里零容忍**(与铁律 #1 的 token 纪律同源)。唯一豁免:
  数据可视化的系列色(图表多系列配色),必须集中定义成常量并注释豁免理由。
- `--text-*` / `--bg-*` / `--border-*` 是映射到 `--nm-*` 的**别名层**
  (同一来源,不是第二套调色板),两者都合法;同一文件内保持一致即可。

> 现状债(收敛 PR 处理,写新代码不许再加):`--color-red-500` 直连 75 处、
> `--color-yellow-500` 直连 61 处、tsx 内 hex 32 处(清单见附录)。
> (2026-08-11 收敛后:调色板直连与非豁免 hex 已清零。)

### 2.5 表面层级(surface ladder)

中性底色是一个**封闭的四层阶梯**,组件按职责取层,不按局部好看取色
(2026-08-11 Owner 对照 team/单聊双截图定案——此前两个 composer 各选各的
灰,就是因为没有这张表):

| 层 | token | 职责 |
|---|---|---|
| **L0 画布** | `--nm-paper`(=`--bg-deep`) | app 背景、侧栏、页脚 |
| **L1 内容面** | `--nm-card`(=`--bg-primary`) | 主区、卡片、弹窗 |
| **L2 沉底/搁起** | `--nm-paper-warm` / `--nm-raised` | hero 卡、wells、rest 态按钮填充 |
| **选中/悬停纱** | `--nm-row-active` / `--nm-paper-warm`(hover) | 列表行选中、hover 洗色 |

L2 的两个 token 只差 1-2 个色阶,**按交互角色分工、不得混用**
(2026-08-11 审计:悬停洗色曾 61:15 分裂在两个 token 上):

- **悬停洗色(控件/菜单)** → `--nm-paper-warm`
- **选中/激活的持久填充**(segmented 选中项、开启态菜单按钮)→ `--nm-raised`
- **列表行**:选中 → `--nm-row-active`,悬停 → `--nm-row-hover`(同一 ink
  色系的减淡档,约一半强度;2026-08-18 Owner 定案——行的悬停与选中必须是
  同一色相的两个档位,warm 悬停配灰色选中读起来像两种无关高亮)

**规则**:
1. **一屏可辨识的中性底 ≤ 4 层**(行业惯例 3-4;Material 3 / Apple HIG /
   shadcn 的 background/card/muted 同构)。
2. **输入面与其容器必须差一层**:card 容器里的表单件用 warm filled
   (`nm/form` 现状);坐在 L1 内容面上的 **Composer 用 card 白底 +
   hairline 边框、聚焦换 ink 边**(`Composer.tsx` 现状)。同层叠同层
   (白上白、warm 上 warm)靠不住边框救。
3. **同一功能件跨页面必须同层**:单聊和团队房的 composer 是同一个功能件,
   必须长得完全一样——用户对输入框的肌肉记忆是全局的(Slack/Discord/
   Linear 的 composer 在一切房间形态里一致)。任何"这个页面想要更深一点"
   都是在消费层级预算,先查这张表。

---

## 3. 圆角:一套标尺,八个档位

### 3.1 档位表(`index.css:79-86`,这是唯一权威标尺)

| token | 值 | 用途 |
|---|---|---|
| `--radius-xs` | 2px | chip、行内代码、微型控件 |
| `--radius-sm` | 3px | 小控件(按钮、输入框、badge) |
| `--radius-md` | 4px | 默认表面(卡片内的分区、下拉) |
| `--radius-lg` | 4px | 气泡、卡片 |
| `--radius-xl` | 6px | 大面板(侧栏卡、抽屉内容) |
| `--radius-2xl` | 8px | 模态框 |
| `--radius-3xl` | 10px | hero 表面 |
| `--radius-4xl` | 12px | 上限,不再更大 |

**规则**:
- 写法一律 `rounded-[var(--radius-sm)]`;正圆(头像、圆点)用 `rounded-full`。
- **禁止 Tailwind 自带标尺** `rounded-sm/md/lg/xl/2xl`——它们的值
  (4/6/8/12/16px)与本项目同名 token **不同值**(如 Tailwind `lg`=8px,
  token `--radius-lg`=4px)。审计显示 Tailwind 标尺用量(~149 处)反超
  token(~176 处 token 总量中圆角类 ~100 处),这正是"重叠的框圆角
  各说各话"的直接根源。
- 局部方向裁切(`rounded-tr-none` 等)允许,基数仍须来自 token。

### 3.2 嵌套规则(解决"重叠框圆角"的公式)

内外两层圆角框叠放时,同心圆角要成立:

```
内层圆角 = max(0, 外层圆角 − 两层间距(padding))
```

且**内层永远不大于外层**。例:`--radius-2xl`(8px)的模态框内,
padding 16px 的内容卡片圆角应取 0~2px(`--radius-xs` 或直角),
而不是跟外层同档。同档嵌套(外 4px 内 4px 带 padding)会产生
"内框角比外框尖"的错视,就是目前被指出的问题。

---

## 4. 字体与字号

### 4.1 三个字族(`index.css:20-22`)

| token | 字体 | 用途 |
|---|---|---|
| `--font-display` | Space Grotesk | 大标题、hero 数字 |
| `--font-sans` | Inter | 正文、表单、对话内容 |
| `--font-mono` | DM Mono | **系统语言**:标签、状态、时间戳、代码、terminal 流水 |

mono 小标签的固定语法:大写 + `tracking-[0.10em]`~`[0.22em]` 宽字距
(参照 `ui/Badge`、`BracketSectionLabel`)。

### 4.2 字号阶梯(从审计分布提炼,整数 px)

| 档 | 写法 | 用途 | 现用量 |
|---|---|---|---|
| 9px | `text-[9px]` | 版本号、气泡时间戳等最小 mono 注记 | 32 |
| 10px | `text-[10px]` | mono 小标签、次要元信息(**最常用档**) | 239 |
| 11px | `text-[11px]` | 行内状态文字、紧凑列表次行 | 102 |
| 12px | `text-xs` / `text-[12px]` | 辅助正文、按钮文字 | 28+ |
| 13px | `text-[13px]` | 紧凑正文 | 24 |
| 14px | `text-sm` | 标准正文(气泡内容) | 大量 |
| 16px+ | `text-base/lg/xl/2xl/3xl` | 标题阶梯,用 Tailwind 标尺 | ~15 |

**规则**:只取表中档位。**不再发明中间值**——`text-[12.5px]`(11 处)、
`text-[13.5px]`(8 处)、`text-[10.5px]`、`text-[9.5px]`、`text-[11.5px]`
属于历史漂移,收敛 PR 就近归档。

---

## 5. 图标

- **唯一图标库:lucide-react**(线性/stroke 风格)。禁止混入实心(filled)
  图标、emoji 当图标、或其它图标库——"左侧栏有的实心有的线性"这类混排
  是本文要杜绝的第一号问题。确需实心视觉(如选中态)用**语义色填充的
  几何形**(圆点 `StatusDot`、方块)表达,不换图标库。
- 尺寸三档,与字号档对齐:

| 档 | 写法 | 语境 |
|---|---|---|
| 12px | `h-3 w-3` | 行内小图标(10-11px 文字旁) |
| 14px | `h-3.5 w-3.5` | 列表行、按钮内(12-13px 文字旁) |
| 16px | `h-4 w-4` | 标准控件图标(14px 文字旁) |

  更大(`h-5`~`h-8`)只用于空态插图、头像位、hero;不用于行内。
- 同一行内图标与文字**同色**(继承 `currentColor`),需要弱化就弱化整行,
  不单独给图标另配灰度。
- `fill` 属性只允许 `none`/`transparent`/`currentColor`;`stroke-width`
  用 lucide 默认(2),小于 12px 的场景可降到 1.5,不出现第三种值。

---

## 6. 组件选型:nm/ 是现在,ui/ 是存留

现状是**两代设计系统并存**:`components/nm/`(NM primitives,v4 重构的
主体)与 `components/ui/`(第一代)。两边有大量同名重叠(Badge、Button、
Dialog、Textarea、StatStrip 双份)——这是视觉不一致的制度性来源。

**总规则:新界面一律先查 `nm/`;`ui/` 只允许用于下表"存留"列的组件。**
两边都有的,以 `nm/` 为准;`ui/` 侧同名件冻结(不加功能、不进新代码)。

### 6.1 按用途查表

| 我要… | 用 | 不要用 |
|---|---|---|
| 状态/计数小徽章 | `nm/StatusBadge`(带语义色)或 `nm/Badge` | `ui/Badge`(冻结,存量不动) |
| 可删除/可点的标签胶囊 | `nm/Chip`(species 身份色)/ `nm/Tag`(中性) | 自造 span |
| 按钮 | `nm/Button` / `nm/IconButton` / `nm/SplitButton` | `ui/Button`(冻结) |
| 模态框 | `nm/Dialog` / `nm/ConfirmDialog` | `ui/Dialog`(冻结) |
| 抽屉/底部弹层 | `nm/Drawer` / `nm/Sheet` | 自造 fixed 定位层 |
| 悬浮小提示 | `ui/tooltip.tsx`(Radix,**存留**) | title 属性 |
| 悬浮面板 | `ui/popover.tsx`(Radix,**存留**) | 手写 document 监听 |
| 滚动容器 | `ui/scroll-area.tsx`(**存留**) | 裸 overflow-y-auto(嵌套滚动场景) |
| Markdown 渲染 | `ui/Markdown`(**存留**,唯一实现) | — |
| 头像 | `nm/RingAvatar` / `AvatarWithStatus` / `GroupAvatar` / `AvatarStack` | 自造圆形 div |
| 表单件 | `nm/form.tsx`(TextInput/Select/Checkbox/Radio/Toggle/Slider/FormField) | `ui/Input`(冻结) |
| 卡片/表面 | `nm/PaperCard` / `RaisedPanel` / `SunkenWell` | `ui/Card`(冻结) |
| 状态点/加载 | `nm/StatusDot` / `Spinner` / `Skeleton` / `ProgressBar` | 自造 animate-pulse div |
| 空态 | `nm/BracketEmptyState` | 居中灰字 div |
| 区块标签 | `nm/BracketSectionLabel` | 手写 uppercase span |
| 数据统计 | `nm/KPITile` / `StatStrip` / `ChartCard` | `ui/KPICard`(冻结) |
| 提示横幅/toast | `nm/Toast` / `ConnectionBanner` | window.alert(桌面端不可见) |

> 表中"冻结"= 存量代码继续工作,但新代码不引用;何时迁移、是否合并
> 由收敛 PR 逐个处理。`ui/BetaBadge`、`ThemeToggle`、`LanguageToggle`、
> `FeedbackButton` 等无 nm 对应物的照常使用。

### 6.2 图表与可视化

`dataviz` 场景(图表、网络图)的系列色是 hex 零容忍的唯一豁免区,
但必须:集中成常量表、光暗两套、不与语义 token 混用。现有
`NexusNetworkGraph` / `JobDependencyGraph` 的内联 hex 属收敛对象。

---

## 7. 新组件 API 约定(借 shadcn 的"形",不换"神")

技术栈本就是 shadcn 模式(Radix primitives + Tailwind v4 + `cn()`),
新组件遵守同一套约定:

- **`variant` + `size` props**,默认值放解构里;枚举值用语义词
  (`default/accent/success/warning/error/outline`),不用视觉词(`blue/big`)。
- 样式合并一律 `cn()`(clsx + tailwind-merge),`className` 透传放最后,
  让调用方能覆盖。
- `forwardRef` + `...props` 透传原生属性(`ui/Badge` 是范本——`title`、
  `aria-*` 无需声明即可用)。
- **组合优先于配置**:复杂件拆成可组合的子件(`Dialog/DialogContent/
  DialogFooter` 形制),不做 20 个 props 的上帝组件。
- 颜色、圆角、字体只引 token;组件内不出现魔法值。
- 动效用既有 token(`--motion-fast/medium/slow`、`--ease-paper`)与
  `animate-fade-in / slide-up / scale-in` 既有类;不新增 keyframes,
  除非同时登记进 `index.css` 动效区并更新本文。

---

## 8. 明暗主题

- 所有 `--nm-*` / 语义 token 在 `[data-theme]` 下整体重定义,
  **经 token 的颜色自动正确**;hex 与调色板直连在暗色下必然出错
  (`index.css:64` 的注释记录过 `--accent-primary` 光暗反转的教训)。
- 阴影用 `--nm-elev-1/2/3`(带主题变体),不手写 box-shadow。
- 新增表面若两主题需要不同处理,在 token 层加,不在组件里分支。

---

## 9. PR 自查清单(UI 改动)

- [ ] 颜色全部来自 ①② 层 token,无 hex、无 `--color-<hue>-<n>` 直连
- [ ] 中性底按 §2.5 阶梯取层;输入面与容器差一层;同一功能件跨页面同层
- [ ] 圆角全部 `var(--radius-*)` 或 `rounded-full`,无 Tailwind 圆角标尺
- [ ] 嵌套框满足"内 ≤ 外 − 间距"
- [ ] 字号在 §4 阶梯上,无新造中间值
- [ ] 图标 lucide、尺寸三档、无实心混排
- [ ] 组件来自 §6 决策表,没有绕过表格自造轮子、没有引用"冻结"件
- [ ] 新组件符合 §7 API 约定
- [ ] 光暗两主题都看过(至少切一次)

---

## 附录:审计数据(2026-08-11,feat/chat-ui-v4 @ 08d5a627)

**圆角写法分布**(次数):`rounded-full` 99 / `rounded-lg` 84(Tailwind,8px)/
`rounded-[var(--radius-sm)]` 47 / `rounded-xl` 31(Tailwind,12px)/
`rounded-[var(--radius-md)]` 23 / `rounded-md` 20 / `rounded-[var(--radius-xs)]` 15 /
`rounded-[var(--radius-lg)]` 15 / `rounded-sm` 7 / `rounded-2xl` 7 / 其它零星。
→ Tailwind 标尺与 token 标尺约 149 : 100 并行。

**字号分布**(px 档,次数):10px 239 / 11px 102 / 9px 32 / 12px 28 / 13px 24 /
12.5px 11 / 13.5px 8 / 11.5px 7 / 10.5px 6 / 9.5px 1 / 8px 3 / 其余为 Tailwind 标题档。

**图标尺寸分布**:`h-3` 194 / `h-4` 187 / `h-3.5` 134 / `h-8` 38 / `h-5` 32 /
`h-6` 29 / `h-7` 22。lucide 引用文件数 94。

**颜色**:tsx 内 hex 32 处,分布在 9 个文件(OneKeyOnboard、
ArtifactDownloadMenu、BusFailuresSection、InnerThoughtCard、SlackConfig、
TeamManagementModal、ArenaProvisioningModal、NexusNetworkGraph、
JobDependencyGraph——后两个属图表豁免区,需集中化);
`--color-red-500` 直连 75 处、`--color-yellow-500` 直连 61 处。

**组件重叠**:`Badge`、`Button`、`Dialog`、`Textarea`、`StatStrip` 在
`nm/` 与 `ui/` 双份;徽章类共 5 件(ui/Badge、nm/Badge、nm/Chip、nm/Tag、
nm/StatusBadge)。

**动效**:`animate-spin` 113 / `animate-pulse` 29 / `animate-fade-in` 26 /
`animate-slide-up` 12 / 其余零星——现有类已覆盖全部需求,无需新增。
