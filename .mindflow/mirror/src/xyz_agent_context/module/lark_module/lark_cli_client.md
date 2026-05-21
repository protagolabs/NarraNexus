---
code_file: src/xyz_agent_context/module/lark_module/lark_cli_client.py
stub: false
last_verified: 2026-05-21
---

## Why it exists

Unified async wrapper around every `lark-cli` subprocess call. Turns the
CLI into a single-function API: call `_run_with_agent_id(args, agent_id)`
and the client handles credential lookup, workspace hydration, and HOME
isolation transparently.

## Design decisions

- **DB is source of truth, workspace is derived.** Every agent's Lark
  state (app_id, plain app_secret, profile name, brand) lives in
  `lark_credentials`. The per-agent workspace (`~/.narranexus/lark_workspaces/<id>/`)
  is a view that can be rebuilt from DB at any time.
- **`_ensure_hydrated(cred)` is idempotent.** Before every agent-scoped
  call we check `workspace/.lark-cli/config.json` — if it already lists
  `cred.app_id`, we skip; otherwise we rebuild by running
  `lark-cli config init --app-id X --app-secret-stdin --name Y --brand Z`
  with HOME=workspace. Plain secret flows DB → stdin → CLI, never
  touches args.
- **Single workspace, single profile, no `--profile` flag.** Because
  each workspace contains exactly one active profile, we never need
  `--profile` on subsequent commands. This matches how a single-machine
  user naturally uses `lark-cli`.
- **Lazy migration for legacy manual binds.** Pre-refactor manual binds
  had `workspace_path=""` in DB. On first call, `_run_with_agent_id`
  computes the path, persists it, and hydrates — no startup migration
  script needed.
- **`_run_with_home` kept for one special case** — `config init --new`
  during `lark_setup` creates the credential itself, so it runs before
  any DB row exists and bypasses hydration.
- **`shell=False` everywhere**, secrets via `stdin_data`, timeout kills
  the subprocess. All unchanged from V1.

## Upstream / downstream

- **Upstream**: every `_lark_mcp_tools.py` tool, `_lark_service.do_bind`
  (which uses `_run_with_agent_id` to verify credentials by hitting bot
  info), `lark_trigger.py` (for `get_user`, bot open_id lookup,
  `_resolve_sender_name`), `lark_context_builder.py` (`list_chat_messages`),
  `lark_module.py` (`send_message`), `backend/routes/lark.py` (unbind),
  `backend/routes/auth.py` (delete_agent).
- **Downstream**: `lark-cli` binary, `_lark_workspace.py` (paths + HOME
  env), `_lark_credential_manager.py` (cred fetch, lazy migration
  persistence).

## Gotchas

- Hydration triggers a real `config init` subprocess. First call on a
  cold workspace can take a couple of seconds. Subsequent calls are fast
  (idempotence check is a single file read + JSON parse).
- If DB has no plain secret (agent-assisted setups before
  `lark_enable_receive`), hydration fails deterministically with an
  actionable error telling the caller to complete Phase 2.
- Debug logs include the full `lark-cli` command. Secrets are passed via
  `stdin_data`, so they never appear in logs — keep it that way.
- `_exec_lark_cli` is private; external callers must go through
  `_run_with_agent_id` (typical), `_run_with_home` (config init --new),
  or the business methods (`send_message`, `get_user`, etc.).

## Phase 1c additions — binary download path

- **`capture_binary` kwarg on `_exec_lark_cli` (+ forwarded by
  `_run_with_agent_id`).** When set, stdout is treated as a status
  channel rather than a JSON payload. lark-cli's `api ... --output
  <path>` writes the response body to disk and emits an empty stdout
  on success (or a JSON error envelope on failure); parsing an empty
  string as JSON would have raised, so the new mode skips that step
  and returns `{"success": True}` with no `data` field. Error handling
  is unchanged — non-zero exit still surfaces the JSON error envelope
  the same way the text path does.

- **`fetch_message_resource(agent_id, *, message_id, file_key,
  resource_type, timeout=60.0) -> bytes`.** Async wrapper around
  `api GET /open-apis/im/v1/messages/{id}/resources/{key}` with
  `--params {"type": "..."}` and `--output <tmpfile>`. Reads the
  tmpfile back as bytes, cleans up in `finally` (so both success and
  error paths leave no leaked temp files). Raises `RuntimeError` on
  CLI failure / empty output / unreadable file — the trigger's
  `fetch_attachments` catches and audits, preserving never-raise at
  the trigger boundary.

  `resource_type ∈ {"file", "image", "audio", "video", "media"}` per
  Lark's IM resource endpoint contract.

  Uses `tempfile.NamedTemporaryFile(delete=False)` to obtain a path
  the CLI can write to under the current process's `/tmp`; explicit
  `os.unlink` in `finally`. The PRP plan flagged the option to use
  the workspace's tmp subdir under disk-pressure, but the default
  is fine for most attachments and avoids a hard dependency on the
  workspace being hydrated before fetch.
