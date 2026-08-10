---
code_file: src/xyz_agent_context/module/identity/verify.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The ONE header-level identity verification (PR #260 review Important #3):
mcp middleware and backend's nx-agent service path ran the identical
algorithm (explicit header or bearer field #7 → Ed25519 verify → cross-check
bearer user_id vs proven sub) as two near-identical copies, with a third
consumer already forecast. Copies drift; the algorithm now lives here once.

## The split that matters

Key-availability POLICY is deliberately NOT here. Whether a missing public
key fails open (mcp — data plane, a keyless deploy has no signer either) or
fails closed (backend — an nx-agent bearer is always a service call) is the
one real difference between the verifiers, so each caller loads the key and
chooses its own degradation; this function only answers "given this key, is
this proof good?" with a stable reason string for their logs.

## 上下游

- 消费方: [[mcp_auth]] `_verify_headers` / `verified_caller_for_tool_call`,
  [[../../backend/auth.py|backend/auth]] `_verify_nx_service_bearer`
- 依赖: [[tokens]] `verify_identity_token` + `_mcp_identity` 公开面
  `parse_bearer_identity`
