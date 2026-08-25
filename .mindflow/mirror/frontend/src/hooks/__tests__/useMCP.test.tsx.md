---
code_file: frontend/src/hooks/__tests__/useMCP.test.tsx
last_verified: 2026-08-25
stub: false
---

# useMCP.test.tsx — useMCPList 的取数契约

用 `QueryClientProvider` 包一层真实 TanStack Query 运行时(不是替身
query 对象),验证 [[useMCP.ts]] 的两条契约:agent 选中时按当前
`agentId` 调 `api.listMCPs` 并解出 `.mcps`;没有 active agent 时
`enabled` 门槛拦住请求,`api.listMCPs` 完全不被调用。
