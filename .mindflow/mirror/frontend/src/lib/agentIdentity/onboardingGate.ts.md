---
code_file: frontend/src/lib/agentIdentity/onboardingGate.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 移入 `lib/agentIdentity/`

与 agentIdSafety / defaultAgent / guideAgent / onboardingGate 同组：「这个 agent id 能不能用、
该选哪个 agent、哪个是引导 agent、用户是否还欠欢迎流程」。原先平铺在 67 个文件的 `lib/` 里，
按 `netmindAuth/` 先例收成子目录（无 index 桶文件，直接按模块路径导入，`vi.mock` 目标保持精确）。

## 2026-10-30 — 「用户自己的 agent」只数 `created_by === userId`

`humanAgents` 原来是「列表里除引导 agent 外的全部」，而列表含全平台公开 agent——只要平台上
有任何公开 agent，新用户就被判成老用户、写入 `landing_completed`、欢迎流程被跳过。现在只数
查看者自己的、且不是引导 agent 的；引导 agent 由 `pickGuideAgent(agents, userId)` 认（只认自己的）。

## 2026-09-03 (评审修订) — 「哪些卡是 login 自动开的」交回后端

删掉 `AUTO_PROVISIONED_PROVIDER_SOURCES`，改读每张卡的 `auto_provisioned`
（GET /api/providers，由后端在 provisioner 旁派生）。前端那份集合与后端事实没有机械约束，
后端多开一张卡的那天所有新用户都会被判成老用户、首程流程静默消失——正是本文件存在
要防的 bug。顺带删掉一行紧接着被覆盖的死 `inFlight.set`（M7）。

# lib/agentIdentity/onboardingGate.ts — "does this user still owe the first-run flow?"

## Why it exists

Because `/` is only one way into the app. The first version of this gate lived in
[[App]]'s `RootRedirect`, which meant a brand-new account could walk straight
past [[WelcomePage]] by arriving any other way: a `?next=/app/chat` login (what
the website CTAs and the /pay bounce use), a bookmark, or a plain refresh on a
protected route. Owner caught it 2026-08-27 — "a new account must go through
welcome first". So the question moved here and `ProtectedRoute` asks it, which
covers every protected entry point at once.

## Design decisions

- **Server-side answer** (`onboarding_progress.landing_completed`), not
  localStorage: a second browser or machine must not replay the flow.
- **Cached per userId in module scope + in-flight dedupe.** It is asked on every
  protected mount; N simultaneous mounts cause ONE request, and the truth only
  changes when the flow writes the flag.
- **`markWelcomeSeen()` flips the cache before the flow navigates away.**
  Without it, ProtectedRoute would re-run the gate on the destination route,
  read a stale "still owes it", and bounce the user back into the flow.
- **Existing users are backfilled silently** — but "existing" has to mean
  something a HUMAN did, because login itself creates artifacts. The first
  version counted "any agent or any provider" and so misclassified EVERY new
  account (Owner report 2026-08-27: a fresh login landed straight in the chat,
  flag written behind their back). Login provisions a guide agent for every new
  user, and auto-registers NetMind provider cards on the Power path. The probes
  therefore discount both: they count only the viewer's own agents other than
  the guide ([[guideAgent]]'s `pickGuideAgent`), and providers whose `source` is not in
  `AUTO_PROVISIONED_PROVIDER_SOURCES`.
- **A failing probe means "let them in"**, never "hold them". Onboarding state is
  not worth blocking someone's first minute over; the worst case is asking again
  next session.
- `clearOnboardingGateCache()` is called by [[sessionWipe]] — module scope
  survives a store reset, so the next user must not inherit this answer.
