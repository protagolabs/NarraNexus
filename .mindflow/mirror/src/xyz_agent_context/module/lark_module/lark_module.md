---
code_file: src/xyz_agent_context/module/lark_module/lark_module.py
stub: false
last_verified: 2026-04-23
---

## 2026-04-23 update — incremental scope authorization guide

Added `_INCREMENTAL_AUTH_GUIDE` constant and wired it into the
`stage=="completed"` branch of `get_instructions`. Motivated by the
xinyao_test_v1 prod incident 2026-04-22 where the agent minted 6
separate `auth login --scope X --no-wait` URLs inside 13 minutes
without ever polling the device_code from any of them.

Why a new prompt block instead of reworking state/flow: the CLI
primitives (`--no-wait` + `--device-code`) already support the correct
two-step flow. What was missing was the agent-side discipline to
(a) poll with the previous turn's device_code instead of re-minting,
and (b) not poll inside the same turn as the mint. Both were absent
from `_IDENTITY_GUIDE`'s one-line missing_scope bullet. The new guide
explicitly scripts Step 1 (this turn: mint, send URL, stop), Step 2
(next turn: poll with the prior device_code, retry original command),
the "do not mint while a URL is in flight" rule, and the
`authorization_pending` error translation. Pins the two-turn
boundary into the prompt so future LLMs / prompt edits can't regress
it silently; the pinning is enforced by
`tests/lark_module/test_incremental_auth_guide.py`.

Gated on `stage == "completed"` for the same reason `_IDENTITY_GUIDE`
is — during onboarding the three-click flow handles authorization
end-to-end and this guidance would be confusing noise.

The `lark_cli` tool docstring in `_lark_mcp_tools.py` was updated to
point at this section rather than restate the incomplete one-liner.

## 2026-04-22 update — C-mini redesign (three-click authorization)

The `get_instructions` render and `hook_data_gathering` were both reworked
as part of the Lark three-click authorization redesign. See
`reference/self_notebook/specs/2026-04-22-lark-three-click-auth-design.md`
for the full rationale.

### What changed

- **Matrix reduced** from 5 binary rows + big narrative block to 3 core
  rows (App / Permissions / Real-time receive) + 1 optional row
  (Visibility). Permission row renders a single `stage` string
  (`not_started` | `waiting_admin` | `waiting_user_click` | `completed`)
  produced by `LarkCredential.current_click_stage()`.
- **Three-click background section** (`_THREE_CLICK_BACKGROUND`) prepended
  to the matrix during configuration, dropped once `stage=completed`.
  This is the ONLY place the Agent learns about the enterprise-tenant
  three-click flow — upstream `lark-shared` SKILL.md is out of our
  control and describes a single-click model. By not touching SKILL.md
  and keeping the correct model inline in `get_instructions`, we win
  on every rendered turn.
- **Coach section** is now strict `stage → single tool call` mapping.
  Every branch is gated on DB state, never on user's literal words
  ("done / 完成了 / 点了" can mean any of Click 1/2/3 — ambiguous by
  design). The branch for `waiting_admin` even spells out
  "if user said 点了 without mentioning admin, still WAIT."
- **Iron rules condensed 16 → 6** (`_IRON_RULES` constant). Deleted
  duplicates (multiple MCP-only restatements, chained-injection variants,
  default-`--as bot` repetition) and literal-word triggers
  ("when user says 'done' → call lark_auth_complete") which are now
  handled inside `lark_permission_advance`.
- **Skill section** (`_build_skill_section`) renders ONLY when
  `stage == completed`. Saves ~600 tokens during configuration where
  the Agent shouldn't be learning `im +messages-send` syntax yet.
  It also carries the "Lark skill files live in the MCP container, not
  your workspace — use `lark_skill(agent_id, name, path)`, never
  `Read`/`Glob`/`Grep`" rule. This rule must be surfaced in all three
  places that teach the Agent about lark_skill (docstring in
  `_lark_mcp_tools.py`; banner prepended by `_lark_skill_loader.py`;
  this system prompt section). Drift in any one undermines the other
  two — see `2026-04-22` post-C-mini link-rewrite change in
  `_lark_skill_loader.md`.
- **P4 fix in `hook_data_gathering`**: removed the `if cred and cred.is_active`
  gate. Now injects `lark_info` for ANY credential row (including
  `pending_setup` / `is_active=False`) so the Matrix can show
  `⏳ creating` during the 15s window between `lark_setup` return and
  `_finalize_setup` completion. Without this fix, Agent would see
  "No Lark bot bound" during that window and try `lark_setup` again
  (which errors with already-exists).
- **`lark_info` schema simplified**: fields `user_oauth_ok`,
  `console_setup_ok`, `bot_scopes_confirmed`, `pending_oauth_url`,
  `pending_oauth_device_code` removed. Single new field `stage` replaces
  them all. `is_owner_interacting` / `current_sender_id` /
  `owner_open_id` / `owner_name` / `receive_enabled` /
  `availability_confirmed` retained.

### Token budget (estimated)

- Unbound: ~500 tokens (mostly iron rules)
- Configuring: ~900 tokens (includes three-click background + matrix + coach)
- Fully configured: ~1200 tokens (swap background for skill section)

## Why it exists

Entry point for the Lark/Feishu integration. Registers the module with
the framework, creates the MCP server, injects Lark credential info into
the agent's context via `hook_data_gathering`, and registers a channel
sender so other modules can send Lark messages on behalf of an agent.

## Design decisions

- **`module_type = "capability"`** — auto-loaded for every agent; no
  LLM judgment needed to activate. The module contributes context and
  MCP tools regardless of whether a bot is bound.
- **MCP port 7830** — chosen to avoid collision with MessageBusModule
  (7820) and earlier modules (7801-7806).
- **`ChannelSenderRegistry.register("lark", ...)`** — class-level
  `_sender_registered` flag ensures the sender is registered exactly
  once across all LarkModule instances.
- **`get_config()` is `@staticmethod`** — matches the framework contract
  where `MODULE_MAP` may call it without an instance.
- **Static instruction fragments as module-level constants**
  (`_NO_BOT_INSTRUCTION`, `_THREE_CLICK_BACKGROUND`, `_IRON_RULES`):
  wording stays identical across turns, and cheap f-string concatenation
  lets `get_instructions` focus on state → section routing only.

## Upstream / downstream

- **Upstream**: `module/__init__.py` (MODULE_MAP), `module_service.py`.
- **Downstream**: `_lark_mcp_tools.py` (tool registration),
  `_lark_credential_manager.py` (`current_click_stage` drives matrix;
  `hook_data_gathering` reads `permission_state`),
  `ChannelSenderRegistry` (send function),
  `_lark_skill_loader.py` (`get_available_skills` inside
  `_build_skill_section`).

## Gotchas

- `hook_after_event_execution` compares `str(ws)` against
  `WorkingSource.LARK.value` because `working_source` may arrive as
  either the enum or its string representation.
- `_build_skill_section` swallows all exceptions in `get_available_skills`
  — a broken skill loader must not crash instruction rendering.
- The instruction string concatenates fragments without Markdown separators
  between them; verify rendering on a live turn if the format looks off
  (some fragments end with a blank line, some don't).
- Token budget is tight — adding any new always-rendered block must
  trade off against something in `_IRON_RULES` or the matrix, not just
  piled on top.
