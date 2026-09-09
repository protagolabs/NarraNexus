---
code_file: frontend/src/services/wsCircuitOpen.ts
last_verified: 2026-09-09
stub: false
---
# wsCircuitOpen.ts — 检测 WS "熔断器打开" 帧 + 横幅自愈判定

## 为什么存在

后端 fresh-run 闸门在 Agent 被熔断（paused/cooling）时发一帧
`{type:'error', error_type:'agent_circuit_open', cb_reason:'paused:auth'|'paused:quota'|'cooling'}`
并关闭 socket。若无处理，用户只看到一个红气泡。这个 helper 让 wsManager 识别该帧并派发
app 级事件，App.tsx 弹出带"Resume"按钮的横幅——与 auth-expired 路径（wsAuthError.ts）对称。

2026-09-09 起还导出 `shouldClearCircuitBanner` + `CIRCUIT_BREAKER_POLL_INTERVAL_MS`：
横幅原本纯事件驱动、从不回查真实状态，后端半开探测（[[circuit_breaker]]）成功自愈后
横幅仍会挂在屏幕上直到用户手动重试/关闭。App.tsx 现在在横幅显示"paused"期间轮询既有
状态端点，这两个导出是"要不要关闭横幅"的判定逻辑，从轮询回调里抽出来单测。

## 上下游关系

`isCircuitOpenMessage` / `circuitOpenReason` 被 `wsManager.run()` 的 onmessage 调用；
`dispatchAgentCircuitOpen` 派发 `narranexus:agent-circuit-open`（detail: {agentId, reason}），
App.tsx 监听后渲染横幅，其 Resume 按钮调 `api.resetAgentCircuitBreaker`；`shouldClearCircuitBanner`
被 App.tsx 新增的轮询 `useEffect`（`api.getAgentCircuitBreaker`）调用，判定为真则清空横幅 state。

## 设计决策

从 wsManager 抽出以便单测（无需真 WebSocket）。只在 fresh-run 路径出现（reconnect 针对
已存在的 run，不过 fresh-run 闸门），所以只 wsManager.run() 接线。

轮询复用**已有**的 `GET /{agent_id}/circuit-breaker` 端点（`agents_circuit_breaker.py`,
之前有实现但零调用方）——刻意不新开后端接口，只是给一个已存在却从未被消费的端点接上第一个
调用方。`shouldClearCircuitBanner` 判定刻意宽松（非 `paused` 即清）：`cooling` 也清是因为
横幅文案本就区分 paused/cooling 两种措辞，轮询只关心"还需不需要挂着"而不重新分类原因。
