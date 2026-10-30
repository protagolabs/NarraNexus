---
code_file: frontend/src/lib/agentIdentity/guideAgent.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 移入 `lib/agentIdentity/`

与 agentIdSafety / defaultAgent / guideAgent / onboardingGate 同组：「这个 agent id 能不能用、
该选哪个 agent、哪个是引导 agent、用户是否还欠欢迎流程」。原先平铺在 67 个文件的 `lib/` 里，
按 `netmindAuth/` 先例收成子目录（无 index 桶文件，直接按模块路径导入，`vi.mock` 目标保持精确）。

## 2026-10-30 — 只认查看者**自己的** bootstrap_active agent

签名改为 `pickGuideAgent(agents, userId)`，多一条 `created_by === userId`。`/api/auth/agents`
返回全平台公开 agent，每行都带 `bootstrap_active`（还没聊满 3 轮的 agent 为真），陌生人还在 bootstrap 期的公开 agent
会被当成「你的引导 agent」：StepAgent 介绍它、WelcomePage 的 openGuideAgent 选中它，各面板
以非 owner 身份成串 403（监控 v2 按失败数封 IP）。三个调用方（[[WelcomePage]]、[[StepAgent]]、
[[onboardingGate]]）都传 userId。

## 2026-09-03 (评审修订) — 去掉 `?? agents[0]`

没有 bootstrap-active agent 时返回 null，不再把用户自建的第一个 agent 当 guide 扣掉
（门禁会因此多走一次首程；StepAgent 会把用户自己的 agent 介绍成「login 给你建的」）。
StepAgent 已有 null → 轮询/放弃 的分支。

# lib/agentIdentity/guideAgent.ts — which agent is the auto-provisioned guide

## Why it exists

Two places need the same answer and must not disagree: [[welcomeSteps]]
composition (is there an agent step at all?) and [[StepAgent]] (which agent to
introduce). A component-local helper would also have broken fast refresh in the
step file (only components may be exported from a component module).

## Design decisions

- The viewer's own bootstrap-active agent only; no "only agent a fresh account
  has" fallback (removed 2026-09-03). Agents imported from Claude Code / Codex
  are never bootstrap-active, so a user who just imported three sources still
  gets pointed at their guide agent, not at a random import.
- Known limit: `bootstrap_active` is true for every own agent not yet chatted
  with three times, not only the provisioned guide. With provisioning off (or
  the guide deleted), a fresh unchatted own agent is taken for the guide by the
  welcome flow and the onboarding gate. Identifying the guide exactly needs a
  server-side flag for the provisioned agent.
