---
code_file: frontend/src/components/settings/ProviderSettings.tsx
last_verified: 2026-07-09
---

## 2026-07-09 — Section 2 reframed as the GLOBAL DEFAULT; shared consts extracted

Section 2 ("Model Assignment") is relabeled "Global Default" (i18n
section2Title/Subtitle, en+zh): it still writes the user-level ``user_slots``
via the unchanged /api/providers endpoints, but is now explicitly the default
every agent INHERITS — per-agent model/framework overrides live in chat
([[ComposerModelBadge]] + [[AgentLlmConfigPanel]]). The framework list, codex
curated models / allowed sources, recommended helper models, model suggestions,
and getModelsForSlot were extracted to [[agentFramework]] and imported back
(single source of truth shared with the per-agent surfaces); SLOT_DEFS stays
local. Other 8 locales keep their existing section2 translation as fallback.

## 2026-06-17 — 临时屏蔽「自定义 Provider」上传(安全加固)

新增模块级开关 `CUSTOM_PROVIDER_ENABLED = false`。`+ Custom Anthropic /
+ Custom OpenAI` 按钮与协议表单(`showForm` 那段)被该开关 gate 起来,关闭
时改显示一段「Adding custom providers is temporarily unavailable」声明。
原因:用户自定义(任意 base_url)provider 可把 agent 的 LLM 流量指向外部
端点,在 workspace/凭据隔离做完前先关。**表单代码保留、只是 gate**,恢复时
把开关翻成 `true` 即可(对应用户「之后会恢复」的要求)。OneKeyOnboard 预置
接入与已配置 Provider 列表不受影响。后端 `POST /api/providers` 未改(UI 层
屏蔽);后端硬门禁留到整体安全分支。

## 2026-06-14 — 云端非 staff 隐藏 Agent Framework 切换(配合后端 §3 门禁)

后端给 `POST /api/providers/agent-framework` 加了 `is_cloud and not is_staff`
→ 403 的门禁(防凭证骑乘,见 `backend/routes/providers.py.md` 2026-06-14 条目)。
前端若仍渲染可切换的 `<select>`,云端普通用户一切换就吃 403 报错。

前端复用**现成的服务端信号**而非自己重推 cloud+staff:`/claude-status` 与
`/codex-status` 在 cloud 非 staff 时返回 `allowed: false`(两路由同一门禁,恒一致)。
两个 status state 的类型补了 `allowed?: boolean`;派生
`frameworkSwitchBlocked = claudeStatus?.allowed === false || codexStatus?.allowed === false`。
blocked 时 framework `<select>` 换成只读盒子(显示当前 framework + "· managed by
staff in cloud"),非 blocked 走原 `<select>`。两 status 都没加载到时 **fail-open**
(UI 显示控件)——后端 403 仍是真正的安全边界,前端只是体验优化。

## 2026-06-11 (later) — 移除内嵌 OneKeyOnboard(去重)

Section 1 顶部原本渲染 `<OneKeyOnboard>`。现在 SettingsPage 在面板级始终内嵌
OneKeyOnboard、SetupPage 作首屏 hero,二者都把 ProviderSettings 放在
Advanced 折叠里——于是 Advanced 里这个就成了重复。已删除(连同 import)。
Section 1 剩下的是"简单一键预设之外"的部分:model sync、CLI OAuth 登录、
Custom(base_url)端点。`refreshConfig` 仍被其它地方使用,保留。

## 2026-06-11 — helper "Default" option shows the model it resolves to

The helper_llm slot's ``<option value="default">`` used to read just
"Default (recommended)", leaving users unsure what model that actually
runs. It now reads ``Default · <model> (recommended)`` — the concrete
recommended model per provider protocol, from the module-level
``RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`` map (openai → gpt-5.4-mini,
anthropic → claude-haiku-4-5). That map **mirrors backend
``_ONBOARD_HELPER_MODELS``** in model_catalog.py and must stay in sync.
Display-only; the persisted slot value is still the ``"default"``
sentinel (which lets each helper call site pick its own fast model — see
``openai_agents_sdk._resolve_model`` mode 1).

## 2026-06-10 (5th pass) — helper dropdown honors server required_protocols

renderSlotRow's provider filter no longer uses SLOT_DEFS' hardcoded
single protocol for non-agent slots — it reads the SERVER's
required_protocols from GET /api/providers (helper_llm = [openai,
anthropic] since the one-key work). The hardcoded 'openai' was silently
hiding anthropic providers (e.g. a Custom Anthropic key) from the
helper dropdown even though backend assignment + runtime dispatch fully
support them. getProvidersForSlot helper removed (inlined);
no-provider error message lists all accepted protocols.

## 2026-06-10 (4th pass) — helper dropdown hides OAuth providers

The helper_llm provider dropdown now filters out auth_type=oauth rows.
This became urgent after the helper slot opened to the anthropic
protocol: claude_oauth (anthropic) joined codex_oauth (openai) as a
selectable-but-broken option. Server-side mirror gate lives in
user_provider_service.set_slot.

## 2026-06-10 (later) — Quick Add block replaced by shared OneKeyOnboard

The in-component Quick Add (PRESET_PROVIDERS, PRESET_DEFAULT_SLOTS,
selectedPreset/presetKey state, handleQuickAdd, the auto-config
confirmation dialog) is gone — Step 1 now renders the shared
<OneKeyOnboard onComplete={refreshConfig}/>. Two behavior notes:
(1) onboard switches the agent framework, which the old path couldn't
(official OpenAI keys were impossible via Quick Add); (2) the old
"Update" affordance for an already-added preset was effectively broken
anyway (add_provider raises 'already exists'), so nothing real was
lost — key rotation belongs to a future edit-provider flow.

## 2026-06-10 (later) — accurate codex no-provider message

The agent slot's "No openai protocol provider configured" error was
misleading under framework=codex_cli when the user HAS an openai
provider that is merely codex-ineligible (aggregators don't expose the
Responses API and are filtered by CODEX_ALLOWED_PROVIDER_SOURCES). The
codex branch now explains: codex login or Custom OpenAI key; NetMind /
Yunwu / OpenRouter not supported.

## 2026-06-10 — demoted to "Advanced" on first-run (unchanged internally)

No code change beyond the merge cleanup; noting placement: on /setup this
component now lives behind the "Advanced setup" disclosure (OneKeyOnboard is
the primary surface). On /app/settings it remains the full provider UI.

## 2026-06-10 — Agent slot reasoning dropdowns (Thinking / Reasoning Effort)

The agent slot card gained two selects bound to the framework-neutral
SlotConfig params: Thinking (Auto/On/Off) and Reasoning Effort
(Auto/Low/Medium/High/Max). Auto = '' = the backend adapter passes
nothing (framework default — today's behavior). Wiring notes:

- `handleLocalSlotChange` now PRESERVES the effective reasoning params
  when the provider/model dropdowns change — switching model must not
  silently reset the knobs.
- `handleLocalReasoningChange` stages a param change; it no-ops until a
  provider is selected (the selects are disabled in that state).
- `handleApply` always sends `thinking`/`reasoning_effort` in the PUT
  body (PUT semantics: '' resets to auto server-side).
- Rendered only for `slot.key === 'agent'`; helper_llm doesn't get the
  knobs yet (its OpenAI adapter mapping is future work).


## 2026-06-08 (evening) — Drop A/B aliases entirely, single canonical name

Cleanup pass after the afternoon cutover: backend now registers ONLY
`codex_cli` (no `codex_cli_v2` / `codex_official` / `codex`
aliases), so the frontend `CODEX_FRAMEWORK_IDS` set collapses to
just one element — replaced with a direct `=== 'codex_cli'`
equality in the `isCodexFramework` helper. The helper is kept
(rather than inlined at three call sites) so a future v3 framework
id lands in one spot.

Per binding rule #2 (YOLO, no backwards-compat shims), DB rows
still holding the dropped A/B aliases (`codex_cli_v2`,
`codex_official`) fail loud on next turn — the user re-picks
"Codex CLI" from Settings to fix. This was an explicit choice
over a silent startup migration: cleaner code, one-time minor
user friction, no automation that has to keep working forever.

v1 source file (`xyz_codex_cli_sdk.py`) intentionally kept in the
repo as revival fallback — if v2 has a critical regression we can
flip one `register_agent_loop_driver` line in
`agent_framework/__init__.py` to bring v1 back online without
revert.

## 2026-06-08 (afternoon) — Cutover: dropdown shows ONE Codex CLI

Phase 3 cutover: backend now aliases every codex framework name
(`codex`/`codex_cli`/`codex_cli_v2`/`codex_official`) to the
official-SDK driver. Dropdown reverts to a single "Codex CLI" entry —
v1/v2 distinction is gone at the UI layer.

(superseded by the cleanup pass above — `codex_cli` is now the only
registered codex name.)

## 2026-06-08 — Agent framework dropdown exposes Codex CLI v2

`AGENT_FRAMEWORKS` now lists three entries instead of two:

- `claude_code` (Claude Code)
- `codex_cli` (Codex CLI v1 — manual subprocess)
- `codex_cli_v2` (Codex CLI v2 — official `openai-codex` Python SDK, streaming reasoning + RPC interrupt)

The dropdown is the only end-user path to opt into v2 — direct SQL on `user_slots.agent_framework` is blocked by sqlite_proxy holding the WAL lock while backend is running.

To avoid five scattered `agentFramework === 'codex_cli'` checks drifting as more codex variants land, a module-level helper centralizes the check:

```ts
const CODEX_FRAMEWORK_IDS = new Set(['codex_cli', 'codex_cli_v2', 'codex_official'])
const isCodexFramework = (framework) => CODEX_FRAMEWORK_IDS.has(framework || '')
```

This mirrors the backend's `provider_driver/resolver._CODEX_FRAMEWORK_VALUES` — same name, same shape, same purpose. **Adding a v3 framework name later means one edit in each file, not five scattered string comparisons.** Three call sites in this file use the helper: model curation (`getModelsForSlot`), provider source filter (`renderSlotRow`), and the install banner condition.

## 2026-05-18 — `authFetch` 必须发 `X-User-Id`（修跨用户写入 bug）

之前 `authFetch` 只发 JWT Bearer，不发 X-User-Id。Local 模式下 backend middleware 看到 header 缺失就 fallback 到"users 表第一行"，导致 binliang3 在 Settings 页面填的 NetMind API key 全部写到了 binliang（最老账号）名下。后端这次彻底关掉了 fallback（缺 header 直接 401），所以这里也必须配合发上来。

同时 `providerUrl()` 删除了 `?user_id=...` 这条 query 通道——和后端一致，identity 只走 header。这条提交里同步更新的还有 `App.tsx` 和 `SetupPage.tsx` 的 bare `fetch(...?user_id=...)` 调用，统一改走 `api.getProviders()`（ApiClient 自动发 X-User-Id 和 JWT）。

`syncProviderDefaults` 的签名也从 `(userId: string)` 改成 `()`——参数没意义了。

## 2026-05-31 — Agent slot label follows selected framework

The Agent slot provider dropdown already changes protocol based on the
selected framework (`claude_code` → Anthropic, `codex_cli` → OpenAI).
The row subtitle now follows the same state, showing `Main dialogue
(Claude Code)` or `Main dialogue (Codex CLI)` instead of a fixed
Anthropic-only label. This keeps the UI aligned with the backend's
framework-dependent slot validation.


## 2026-05-14 — Quick Add auto-fills empty slots (NetMind)

`handleQuickAdd` now sends `default_slots` so a brand-new user with just
an API key is immediately usable — no manual slot wiring.

- `PRESET_DEFAULT_SLOTS` maps a preset → recommended `{protocol, model}`
  per slot. Only `netmind` is wired up: one NetMind key creates both an
  Anthropic- and an OpenAI-protocol endpoint, so all three slots fill
  from one key — `agent` → DeepSeek V4 Pro (anthropic), `helper_llm` →
  DeepSeek V4 Flash (openai), `embedding` → BGE-M3 (openai). Model ids
  must match `model_catalog.py` `DEFAULT_MODELS[("netmind", ...)]`.
- Only **empty** slots are filled — `handleQuickAdd` skips any slot that
  already has a `config`. The backend `set_slot` is an upsert, so
  including an already-configured slot would clobber the user's choice.
  This makes the feature safe for the "existing user re-adds NetMind"
  path, not just fresh signups.
- The backend hook (`POST /providers` `default_slots`) already existed
  and was dormant — no backend change; this just started sending it.
- After a Quick Add that auto-filled ≥1 slot, the `autoConfigured`
  state drives a confirmation `Dialog` ("You're ready to go") listing
  what was set and pointing at the slot section for overrides.

# ProviderSettings.tsx — LLM provider CRUD and model-slot assignment

The most complex settings component. Manages two sections:
1. **Provider list** — add (Anthropic, OpenAI, or custom URL), remove,
   show masked API keys.
2. **Model assignment** — three slots (Agent, Embedding, Helper LLM) each
   with a provider + model picker. Changes are staged locally and applied or
   discarded together.

## Why it exists separately from SettingsModal

Provider configuration is stateful (API calls, local form state, multiple
async operations). Keeping it in its own file lets `SettingsModal` stay as a
thin shell and makes provider logic independently testable.

## Upstream / downstream

- **Upstream:** backend REST endpoints under `/api/providers/` and
  `/api/models/` — all called via raw `authFetch` (not the `api` lib)
- **Downstream:** embedded in `SettingsModal` Providers section
- **Auth:** `authFetch` reads the JWT token from localStorage for cloud mode

## Design decisions

**`authFetch` wrapper:** Injects the JWT Bearer header when a token exists in
localStorage. This is how cloud-mode auth works — the same component runs in
both local and cloud mode without branching.

**Staged model assignment:** Users pick Agent/Embedding/Helper models into
local state and explicitly click Apply. This avoids partial saves if the user
changes their mind mid-way.

**Protocol filter on model slots:** The Embedding slot only shows models from
providers with `OpenAI` protocol (embedding API format). The Agent slot only
shows models from providers with `Anthropic` protocol. This prevents the user
from accidentally assigning a chat model to the embedding slot.

## Claude Code Login card — two decoupled state layers

The card surfaces two state layers that must NOT be conflated:

1. **OS credential state** — owned by the `claude` CLI, persisted in
   `~/.claude/.credentials.json`. Drives the Login / Re-login / Logout
   buttons. Backed by `/api/providers/claude-status` (which calls
   `claude auth status` + falls back to the credentials file) and the
   Tauri IPC commands `trigger_claude_login` / `trigger_claude_logout`.
2. **Provider record state** — owned by NarraNexus, persisted in
   `user_providers` (rows where `source='claude_oauth'`). Drives the
   "Add as Provider" / "Remove" affordance and `hasClaude`.

Earlier versions wrapped the entire login UI in `!hasClaude`, which
hid Login/Logout once a provider record existed. That broke account
switching, post-expiry re-auth, and even just seeing which account is
active. Decoupling the two layers means a user can re-login or sign
out without first deleting the provider record — and conversely, can
add/remove the provider without touching OS credentials.

Symmetric end-to-end: backend exposes `email` and `expires_at` in
`claude-status`; the helper `formatExpiresAt()` accepts ISO-8601 or
unix epoch (sec or ms) since the CLI shifts schema across versions.

## Login auto-abort timer

`claude auth login` blocks until the user finishes (or abandons) the
OAuth flow in the browser. Earlier the Tauri command awaited
indefinitely — closing the browser tab without authorizing left the
CLI sitting on a dead callback server forever, with the UI button
stuck on "Logging in...".

Now the Login flow runs a `CLAUDE_LOGIN_TIMEOUT_SEC = 600` countdown:
- `handleClaudeLogin` sets `claudeLoginRemaining` to 600 alongside
  starting the IPC.
- A `useEffect` decrements every second via `setTimeout` (not
  `setInterval`, to avoid the standard "fires while previous handler
  is still pending" trap).
- On hitting 0 the effect fires `cancelClaudeLogin()` → Rust SIGTERMs
  the child → trigger's await resolves with non-zero exit →
  handleClaudeLogin's catch+finally clears UI state.
- The remaining seconds are rendered as `m:ss` inside the Login /
  Re-login button label.

The countdown state is intentionally cleared by handleClaudeLogin's
finally (NOT by the timer effect) so it's authoritative — natural
completion, manual cancel, or timeout all funnel through the same
reset path.

## Gotchas

- This file is large (~400 lines) because it manages five distinct async
  operations with their own loading/error states. Each operation is
  intentionally inline rather than extracted to keep the request/response
  flow readable in one place.
- Model lists are fetched per-provider on demand (when the user expands a
  provider). Caching is local state — refreshing the page re-fetches.
- `getApiBaseUrl()` from `runtimeStore` ensures the correct backend URL is
  used whether running locally or in Tauri mode.
- **`ModelBubbleInput` commit trap** — text typed in the tag input is only
  pushed into `formModels` on Enter / `+` click. If the user types a model
  name and clicks "Add Provider" without committing, the text is silently
  lost and the backend autopopulates defaults (2 Claude models for
  `anthropic` card_type). As of 2026-04-23 the input shows a warning hint
  and pulses the `+` button while uncommitted text exists, to make the
  commit step visible. A stronger fix (auto-flush on submit) was deferred.
