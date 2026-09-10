---
code_file: frontend/src/hooks/useCircuitBannerAutoClear.ts
last_verified: 2026-09-10
stub: false
---

# useCircuitBannerAutoClear.ts — "agent paused" 熔断横幅的自愈轮询

## Why it exists

熔断横幅（App.tsx，`narranexus:agent-circuit-open` 事件驱动）原本从不回查真实状态：
后端半开探测（[[circuit_breaker]]）成功、或 owner 在别的 tab 修好了 key，横幅仍挂着。
2026-09-09 版把轮询写成 App.tsx 里的一个 `useEffect`，预审指出两处缺陷，本 hook 是把
它抽出来修掉并能用 fake timers 单测的结果：

- **依赖收窄**为 `(agentId, reason, isLoggedIn)`，不再依赖 `circuitOpen` 对象本身。
  每一次被拒的 turn 都会派发新 detail 对象；按对象依赖会在用户每次重试时重建
  interval，30s 永远数不满——最需要自愈的场景恰好永远不轮询。
- **登录门禁**：未登录时不轮询鉴权端点（只会刷 401），并直接清掉横幅（App 作为根组件
  不会卸载，登出没有别的路径清它）。
- 挂上即查一次，再每 `CIRCUIT_BREAKER_POLL_INTERVAL_MS` 一次（同文件的过期检查 effect
  是同样的 `check(); setInterval(check)` 范式）。
- 只轮询 `paused*` 原因；cooling 短暂、随下一次交互自然消失。
- 拉取失败保持横幅原样，下一 tick 重试——网络抖动不能误清一个真的还 paused 的横幅。

判定本身在 `services/wsCircuitOpen.shouldClearCircuitBanner`（`active`/`cooling` 才清，
`probing` 不清）；这里只负责节奏与门禁。

## Upstream / downstream

App.tsx 调用 `useCircuitBannerAutoClear(circuitOpen, setCircuitOpen, isLoggedIn)`；
轮询 `api.getAgentCircuitBreaker`（`GET /api/agents/{id}/circuit-breaker`）。

## Tests

`hooks/__tests__/useCircuitBannerAutoClear.test.ts`（fake timers，mock 的是 `api`，不是
判定函数）：立即查 + 30s 后 active 清横幅；同 agent+reason 的新对象不重置 interval；
拉取失败不清、下 tick 重试；cooling 不轮询；登出清横幅且不再轮询；横幅消失即停。
