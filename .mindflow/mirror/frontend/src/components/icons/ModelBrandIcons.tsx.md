---
code_file: frontend/src/components/icons/ModelBrandIcons.tsx
last_verified: 2026-08-20
stub: false
---

# ModelBrandIcons.tsx — real LLM-vendor brand marks

## 为什么存在

[[../../pages/CreateAgentPage.tsx]] 的 Engine 框 Framework / Provider /
Model / Helper LLM 下拉要真实品牌 icon（Owner 反馈）。八个厂商（Claude/
OpenAI/Gemini/GLM(Zhipu)/Kimi(Moonshot)/Qwen/MiniMax/DeepSeek）的官方
monochrome 路径，同 [[ChannelBrandIcons.tsx]] 的做法，从 Simple Icons
（CC0，`cdn.jsdelivr.net/npm/simple-icons@latest/icons/<slug>.svg`）现抓。

## 只放组件，匹配逻辑在别处

这个文件**只导出 React 组件**（`ClaudeBrandIcon` 等 8 个 + 一个
`ProtocolIconComponent` 类型），不导出任何普通函数——`protocol`/
`modelId` → 图标组件的匹配逻辑在
[[../../lib/modelBrandIcons.ts]]（`getProtocolBrandIcon` /
`getModelBrandIcon`）。这是被 ESLint 逼的：react-refresh 插件的
`only-export-components` 规则不允许一个文件混合导出组件和普通函数，
最早一版把两个匹配函数写在这个文件里直接报错。

## 2026-08-20 — 从 currentColor 改成品牌真彩

Owner 反馈"这些 icon 可以是有色的"，`BrandIcon` 新增必填 `color` prop，
每个导出组件传自己的官方 hex：Claude `#D97757`、DeepSeek `#5786FE`、
Gemini `#8E75B2`、Qwen `#6950EF`、MiniMax `#E73562` 都是 Simple Icons
注册表里现成的值；OpenAI 和 Kimi 都是 `#000000`（前者是官方 style guide
自己说的黑白无强调色，后者是 Simple Icons 注册表里 Kimi 条目本身就是
黑）；Zhipu `#3859FF` 来自 Iconify 的 `thesvg-color`（见下方 gotcha，
Zhipu 本来就不在 Simple Icons 里）。

## Zhipu 的 fillRule 坑

Zhipu 图标不在 Simple Icons 目录里，是从 Iconify 的 `thesvg` 集合
（`api.iconify.design/thesvg/zhipu.svg`）抓的，它的 path 用了
`fill-rule="evenodd"`（复杂复合路径，不带这个属性会把镂空的地方填死）。
共享的 `BrandIcon({path, pathFillRule})` 包装组件因此多了一个
`pathFillRule` 参数——命名成 `pathFillRule` 而不是 `fillRule`，是因为
`SVGProps<SVGSVGElement>`（顶层 `<svg>` 自己也认识 `fillRule`，且它的
类型允许 `'inherit'`）跟这里想传给 `<path>` 的
`'evenodd' | 'nonzero'`（更窄）撞了类型，直接叫 `fillRule` 会跟展开的
`...props` 冲突报 TS2322。
