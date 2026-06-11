---
code_file: frontend/src/lib/netmindAuth/tokenInbound.ts
last_verified: 2026-06-11
stub: false
---

# tokenInbound.ts — Power 登录态入站直通（场景 A）

## 为什么存在

用户从 netmind.ai 或 Arena 点链接进入 NarraNexus 时，链接带 `?token=<NetMind loginToken>`。因为我们与 Power 共用 sysCode（token 通用），只要把这个 token 接住、换成我们自己的会话，用户就免登进来了（Phase 1 的「Power 登录态打通」场景 A）。本模块是这条入站链路的纯函数工具，抄自 Arena 的 UserContext 入站处理。

## 上下游关系

**被谁用**：`frontend/src/App.tsx` 的 bootstrap useEffect（App 初始化、早于路由渲染）调 `takeInboundToken(window.location)`，命中则 `exchangeInboundToken` 换会话、写 configStore。

**调用谁**：`exchangeInboundToken` 调 `@/lib/api` 的 `api.netmindLogin(token, source)`（后端 `/api/auth/netmind-login` 验 token 换发我们 JWT）。

## 设计决策

- **即取即删**：`takeInboundToken` 解析后立刻 `window.history.replaceState` 把 `token` 从 URL 抹掉（保留其余 query 与 hash），避免 loginToken 泄漏进浏览器历史/书签/截图。
- **source 透传**：同时读 `?source=`（如 `arena`），即使没有 token 也返回 source——App 会把它暂存 sessionStorage 供 Phase 2 provisioning 用（本阶段只透传不消费）。
- **纯函数边界**：takeInboundToken 只做 URL 解析 + 副作用（replaceState），不碰网络；exchange 单独一个函数，便于测试分离（测试只测 takeInboundToken 的解析与抹除，网络换会话由 api 层测）。

## Gotcha

- App 的 bootstrap effect 在换会话前检查 `isLoggedIn`，已登录则跳过——避免覆盖现有会话。
- 换会话失败时静默 fall through 到登录页（catch 吞掉），不打断用户。
