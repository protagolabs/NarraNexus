---
code_file: frontend/src/lib/guideAgent.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 只认查看者**自己的** bootstrap_active agent

签名改为 `pickGuideAgent(agents, userId)`，多一条 `created_by === userId`。`/api/auth/agents`
返回全平台公开 agent 且每行都算 `bootstrap_active`，陌生人还在 bootstrap 期的公开 agent
会被当成「你的引导 agent」：StepAgent 介绍它、WelcomePage 的 openGuideAgent 选中它，各面板
以非 owner 身份成串 403（监控 v2 按失败数封 IP）。三个调用方（[[WelcomePage]]、[[StepAgent]]、
[[onboardingGate]]）都传 userId。

## 2026-09-03 (评审修订) — 去掉 `?? agents[0]`

没有 bootstrap-active agent 时返回 null，不再把用户自建的第一个 agent 当 guide 扣掉
（门禁会因此多走一次首程；StepAgent 会把用户自己的 agent 介绍成「login 给你建的」）。
StepAgent 已有 null → 轮询/放弃 的分支。

# lib/guideAgent.ts — which agent is the auto-provisioned guide

## Why it exists

Two places need the same answer and must not disagree: [[welcomeSteps]]
composition (is there an agent step at all?) and [[StepAgent]] (which agent to
introduce). A component-local helper would also have broken fast refresh in the
step file (only components may be exported from a component module).

## Design decisions

- `bootstrap_active` first, then "the only agent a fresh account has". Agents
  imported from Claude Code / Codex are never bootstrap-active, so a user who
  just imported three sources still gets pointed at their guide agent, not at a
  random import.
