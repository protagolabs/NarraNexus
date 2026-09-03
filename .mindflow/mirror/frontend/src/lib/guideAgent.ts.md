---
code_file: frontend/src/lib/guideAgent.ts
last_verified: 2026-08-27
stub: false
---

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
