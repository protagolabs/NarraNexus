---
code_file: plugins/builtin.auth.netmind/src/narranexus_plugins/auth_netmind/provider.py
last_verified: 2026-09-04
stub: false
---

# builtin.auth.netmind — provider.py

`NetMindAuthProvider.authenticate(request)` decodes the `Authorization: Bearer` JWT with the backend's `decode_token`; no bearer → `None`; expired/invalid → `AuthError(TOKEN_EXPIRED | TOKEN_INVALID)` (same codes the middleware emitted, so the SPA keeps its session semantics). Returns `{user_id, role, provider, token}`. `CONTRIBUTION` fills `kernel.auth`.
