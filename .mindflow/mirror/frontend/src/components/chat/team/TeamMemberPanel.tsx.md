---
code_file: frontend/src/components/chat/team/TeamMemberPanel.tsx
last_verified: 2026-08-07
stub: false
---

## 2026-08-07 — chrome 头条上的停止按钮 + 三态

running 且有 `event_id` 的成员,头条右侧出现停止按钮。挂在这里是因为这张
卡片已经持有停止所需的两件东西(#219 铺的):`event_id`(就是 run_id)和
live 状态。

**三态 = running →(点击)→ stopping →(观察流给终态)→ stopped**。
`stopping` 只覆盖"请求已记录"到"run 真的停了"之间的空档 —— 那 8 分钟黑箱
要的就是这个即时答复,所以按钮**不等** `api.cancelRun` 的 resolve 才变样,
点下去立刻转 stopping。

`stopping` 有**三条出口**,少一条就会挂住:

- **观察流给终态** —— 正常路径。
- **请求失败**(403 / run 已消失 / 断网)→ 落回可点状态并显示 `stopFailed`,
  而不是永远转着 spinner —— 那等于用新的黑箱换旧的黑箱。
- **`already_settled`** —— run 在渲染与点击之间自己跑完了。此时服务端**没有
  盖旗标**,也就不会有任何终态帧到来;不清掉 `stopping` 就会永远等一个已经
  发生过的事件。语义见 [[api]](「前端据此知道『没什么可停的』,而不是当成
  失败」)。

其余:

- **停止请求按 run 作用域**:state 里存 `{runId, failed}`,`runId` 与当前
  `activity.event_id` 不符就当没有。否则同一成员开下一轮时,按钮会因为上
  一轮的 pending 停止而灰着,而没人要求停这一轮。
- **cancelled 必须与 completed 可区分**:`observation.endState ===
  'cancelled'` 时显示 `stopped`。把用户掐掉的任务显示成"已完成",等于告诉
  他那 25 分钟还是跑完了。
- **`useRunObservation` 保持只读**:取消走独立的 REST 调用,观察通道只负责
  看到结果。它的 mirror 明写"观察绝不启动/停止/引导 run"(铁律 #14),
  从这里发起取消会破坏那个契约。

前端不做 owner 判断 —— 服务端 403 是唯一边界(见
[[runs]]),按钮可见性只是提示。

新 i18n:`chat.team.roster.stop|stopping|stopped|stopFailed|stopHint`,
10 份 locale 齐。测试:`__tests__/TeamMemberPanel.stop.test.tsx`。

## 2026-07-31 (二次) — 观察通道 fatal 错误的诚实展示

live 成员 + observation.errorMessage + 零事件 → 显示 detailLoadFailed，
不再挂着永远兑现不了的 "Starting up…"（配合 hook 的 fatal 停梯）。

# TeamMemberPanel.tsx — 成员详情 = 迷你 ProcessPanel

## 为什么存在

2026-07-31 Owner 反馈：roster 展开后的运行视图（灰点素行的
PhaseTimeline）和单聊 ProcessPanel 是两个世界，「预期是 chat 那个
ProcessPanel 差不多精致的」。本组件把成员详情做成同一张终端卡：
chrome 头条（LiveDot 呼吸灯 + `name · process` + ops/elapsed）、
`✓`/spinner 阶段行、`$` 工具行、`❯▌` 活光标、follow-scroll、plan
页脚 —— 全部复用 processShared 的共享件，与单聊同一套字形语言。

## 数据源（两层，且不是装饰性区分）

- **running/stalled** → [[useRunObservation]](activity.event_id)：
  真·实时 thinking/tool 流（bus 活动行运行中就带 event_id，#199 系列
  铺的钥匙）。观察 socket 连上前（~0-1s）显示诚实的 "Starting up…"
  兜底，不假装知道更多。
- **idle** → useTurnDetail 的持久化 event_log + TurnTimeline 渲染器
  （2026-07-31 已拍板的 reasoning & tools 视图），活在 430px 宽里。
  上一 turn 的结果绝不显示在当前 turn 下（settled key 校验，沿袭
  v1 语义）。

## Gotcha

- `open=false` 时返回 null 且 observation `enabled=false` —— 折叠行
  零 socket。
- stalled 成员观察照常工作（tail-follow 一直跟到心跳死），卡片底部
  额外亮 amber 的 silentFor 行。
- elapsed 用父级共享 `now` 时钟 + poll 的 started_at —— 屏上所有
  时长同一口径（v1 决策沿袭）。
