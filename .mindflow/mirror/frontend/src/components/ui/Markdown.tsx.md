---
code_file: frontend/src/components/ui/Markdown.tsx
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 原始 HTML 先净化再进 DOM

正文来源用户控制不了（agent 回复、队友、inbox 里的外部 IM、bundle 的 Team 简介），而这里跑在 app 同源上，所以 `rehypeRaw` 之后必须紧跟 `rehypeSanitize`，
调用方的 `rehypePlugins` 排在净化**之后**（mention 的 class / test id 才不会被洗掉）。Chrome 实测未净化时 `<iframe srcdoc>`、`<script async src>`（React 19 会提升到 head）、
`<base>`、`<embed>/<object>`、`<form>`、`<style>` 全都能进 DOM。

- schema 基于 `defaultSchema`，只加一处：`strip` 增加 `style`。不在 schema 里的元素默认是**解包**（文字留下），不 strip 的话被删的样式表会变成消息正文。
- **脚注**：sanitizer 给每个 id 加 `user-content-` 前缀，remark-rehype 默认也加一次，id 就变成两层而 href 只有一层，脚注链接落空。所以 `remarkRehypeOptions.clobberPrefix: ''`，
  前缀只由 sanitizer 加；`a` 组件把站内 `#x` 改写成 `#user-content-x`，脚注和 agent 写的锚点都落在加过前缀的 id 上。被否决：`clobber: []`（重新打开 DOM clobbering）。
- **`a` 组件**：`{...props}` 放在 computed `target/rel` 之前。放在后面时 `rel="opener"` 能覆盖 noopener（反向 tabnabbing）。
- 行为变化：原始 HTML 上的 `style` 与 `class` 属性被丢弃（除 schema 白名单），这是有意的——否则 agent 能用 Tailwind 类或 fixed 定位盖住整个界面。
- 依赖：`rehype-sanitize` 钉精确版本 6.0.0，进 `vendor-markdown` manualChunk。

**Gotcha**
- **触发**：往这里传的 rehype 插件把 raw 文本写回 properties → **症状**：绕过净化 → **根因**：调用方插件运行在 sanitizer 之后。
- **触发**：新增依赖 class 的 markdown 样式（如给 raw `<div class>` 写 CSS）→ **症状**：class 被净化掉、样式不生效 → **根因**：`defaultSchema` 只放行 language-*、task list、footnote 几个 class；需要就显式扩 schema。
- 测试走真实 remark/rehype 管线（`components/ui/__tests__/Markdown.test.tsx`），断言看整个 document，因为 React 19 会把部分标签提升到 head。

## 2026-08-14 (二) — rehypePlugins 用真类型

`unknown[]` + `as []` 改成 `PluggableList`（来自 `unified`）。`as []` 是转成"空元组"，
纯粹为了让 tsc 闭嘴——调用方传错东西不会被拦住，而这个 prop 的整个意义就是"把树交给别人
改"，最需要类型的地方反而没有。

`unified` 同时被声明进 `package.json`，见 [[rehypeMentions.ts]] 那条关于幽灵依赖的记录。

## 2026-08-14 — 可选的 rehypePlugins

新增 `rehypePlugins` prop（追加在 `rehypeRaw` 之后）。存在的理由是让调用方**改渲染树**而
不是**改 markdown 源码**：团队房间用正则在源码上高亮 @mention，结果把字面量 `<span>`
塞进了每一个提到 `@all` 的代码块——正则不知道什么是代码块，AST 知道。

传进来的数组必须**稳定**（模块常量或 `useMemo`）：这个组件按浅比较 memo，每次渲染新建一个
数组会让流式回复的每一个 delta 重新解析整段正文。

## 2026-07-30 — React.memo

remark/rehype 每次 render 都全量重解析，而聊天界面每个 WebSocket delta
都重渲染——未 memo 时一个 delta 会重解析**所有**可见历史气泡的 markdown，
主线程饱和表现为「流式卡住→整段一次性蹦出」。props 全是原始值，浅比较
即可跳过未变内容的重解析。

# Markdown.tsx — react-markdown wrapper with GFM, raw HTML, and external-link handling

## 为什么存在

Central point for Markdown rendering so every surface (chat bubbles, awareness text, inbox messages, entity descriptions) gets consistent typography and the same external-link `target="_blank"` behavior.

## 上下游关系
- **被谁用**: `MessageBubble`, `AwarenessPanel`, `InboxPanel`, `AgentInboxPanel`, `EntityCard`.
- **依赖谁**: `react-markdown`, `remark-gfm`, `rehype-raw`, `rehype-sanitize`.

## 设计决策

`rehype-raw` is enabled — the agent backend can send HTML inside Markdown (e.g., from job reports). Everything it parses goes through `rehype-sanitize` right after (see the 2026-10-30 entry): the content is not trusted, even from the user's own agents, because those relay third-party text.

`compact` mode adds `markdown-compact` CSS class — the actual compact styles live in a global stylesheet, not in this file.

## Gotcha / 边界情况

`MarkdownPreview` truncates at `maxLength` characters of the raw Markdown string, not rendered length — the truncation point may fall in the middle of a Markdown construct and produce broken rendering.
