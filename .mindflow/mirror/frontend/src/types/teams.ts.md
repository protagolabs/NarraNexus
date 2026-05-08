---
code_file: frontend/src/types/teams.ts
last_verified: 2026-05-08
stub: false
---

# teams.ts — Frontend types for subproject 1 + 2

把 backend 的 Team / TeamMember / Bundle 各种 request/response shape 镜像成 TypeScript interface。新增类型时同步两端：先在 backend 的 Pydantic 改，再 mirror 到这里，避免运行时字段名 drift。
