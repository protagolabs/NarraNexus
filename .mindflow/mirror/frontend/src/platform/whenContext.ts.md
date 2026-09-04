---
code_file: frontend/src/platform/whenContext.ts
last_verified: 2026-09-04
stub: false
---

# platform/whenContext.ts — WhenContext from the stores

## Intent

`useWhenContext({conversationKind, agentId})` builds the object slot predicates are evaluated against: the agent's module list (when the agents list carries `modules`) for `agentHas:`, the config store's flat keys for `setting:`. The host decides what a setting key means; a plugin only names it. Lives outside `registries/` because registries must not import stores (dependency-cruiser rule).
