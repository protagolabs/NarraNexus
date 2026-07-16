# NetMind billing: key ↔ account ↔ top-up, and how balance-insufficient is handled

**When to read**: touching NetMind key minting/provisioning, the "insufficient
balance/quota" failure path, or the Job-layer no-quota pause; wondering why we
can't pre-check balance or auto-detect a top-up.

## The key → account → balance chain (and where it used to break)

1. **Login** → the frontend holds a NetMind login JWT. `netmind_auth_client.verify_token(jwt)`
   → `NetmindUser(user_system_code, email)` — `user_system_code` is the NetMind
   **account** id. This is the ONLY place account identity is exposed.
2. **Mint** → `netmind_key_client.create_key(jwt)` → `MintedKey(apitoken, token_id)`.
   The minted key is **opaque** — it carries no account id.
3. **Store** → `netmind_provisioner.ensure_netmind_provider` calls
   `UserProviderService.onboard_one_key`, writing the key to `user_providers`
   (one user can have several NetMind providers; a NetMind onboard may create a
   dual `linked_group` of anthropic+openai rows).

Historically step 2/3 dropped the account identity, so given a stored key we
could not tell **which** NetMind account it belonged to. The incident: a user
made several keys from ONE broke account, couldn't tell them apart in the UI,
and topped up a different account — still broke.

**Fix (2026-07)**: at mint time (we still hold the JWT), `ensure_netmind_provider`
now `verify_token`s and stamps `user_providers.netmind_account_id` +
`netmind_account_email` on the user's NetMind rows (additive, nullable columns).
`GET /api/providers` attaches the email per provider, and Settings → Providers
displays "NetMind account: <email>" so the user tops up the RIGHT account.
Capture is best-effort — a failure never fails provisioning (account stays NULL,
shown as absent); existing pre-fix rows stay NULL until the next login/reconfigure.

## Why there is no pre-call balance check

`get_fee_info` (balance) requires the **login JWT**, which the backend does NOT
persist (it lives on the frontend, by design — we store the minted key, never
the JWT). So we cannot check balance before a run, nor poll for a top-up. Only
non-secret account id/email is stored. Detection is therefore **reactive** — the
provider returns the billing error at call time.

Propagation: a NetMind top-up can take **~5 minutes** to take effect (operational
fact from the incident; not code-derivable). The user-facing copy says so.

## How a balance/quota-insufficient failure is handled

Detection is provider-agnostic via `llm_failure.classify_self_serviceable`
(#110): `insufficient balance / insufficient_quota / insufficient funds /
insufficient credit / not enough balance / exceeded your current quota /
402 payment` (also context-window and model-not-found). A bare `429` / rate-limit
is NOT billing — it stays transient.

Two layers, deliberately different (see the two mirror mds for detail):

- **Real-time layer** (`agent_circuit_breaker` + `response_processor` /
  `step_3_agent_loop`): **does NOT pause** on a self-serviceable failure. It
  surfaces an actionable per-turn message and returns — an interactive user reads
  it and acts, and pausing would only block their corrected retry after they top
  up (binding rule #14/#15). This is #110's shipped decision; we keep it.
- **Job layer** (`job_trigger._is_no_quota_failure`): **pauses** — a background
  job has no interactive reader, so the only way to stop the retry storm (the
  9-user / 14-day / 390-retry incident) is `PAUSED_NO_QUOTA`. It **reuses the same
  `classify_self_serviceable`** (single source of truth), and auto-resumes via
  the existing edge recovery (`rearm_user_no_quota_jobs` on provider save) + the
  15-min backstop scan (`_resume_eligible_no_quota_jobs`) — once the owner tops
  up / reconfigures, the readiness check flips it back to ACTIVE, and either the
  run succeeds or (still no balance) it re-pauses. That backstop IS the ~15-min
  probe; no separate probe was added.
