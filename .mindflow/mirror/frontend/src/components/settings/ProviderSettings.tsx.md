---
code_file: frontend/src/components/settings/ProviderSettings.tsx
last_verified: 2026-08-24
---

## 2026-08-24 (4) — (1)~(3) 抽取的真正动机：Create Agent 向导要复用同一套逻辑

(1)/(2)/(3) 三条各自记录了 `ModelBubbleInput` / `CustomEndpointForm` /
`CliSignInPanel` 从本文件搬出去这件事本身,但只从"本文件瘦身"的角度写。补
一条从**调用方视角**的总结：这三次抽取不是为了 Settings 页面自己变干净就
够了——真正把它们从"内联在本文件里的可以但没必要"升级成"必须抽出来"的,是
新的 [[../../pages/CreateAgentPage]] Step 1（provider 选择/添加步骤,见该文
件 2026-08-24 (14) 条目）需要一模一样的"添加 provider"逻辑：一键预设、自定
义 endpoint、CLI 登录三条路径,跟 Settings 这边的 add-provider 弹窗要做的事
完全一样。如果不抽,这 ~700 行（三个组件的原内联代码合计）就得在两个页面
各写一份,而且两份会随时间漂移（字段校验、错误文案、`testResult` 清空时机
这类细节最容易先在一处改了另一处忘改）。

抽取之后职责边界很干净：

- `ProviderSettings.tsx`（本文件）现在只剩四件事——① provider 卡片网格
  （`providerList` 渲染 + "+ Add provider" 卡片）、② provider 详情弹窗
  （查看/Test/Edit models/Delete）、③ edit-models 弹窗（复用
  [[../providers/ModelBubbleInput]]）、④ add-modal 的 tab 切换外壳
  （`addMethod: 'onekey' | 'oauth' | 'custom'`,三个 tab 分别渲染
  `OneKeyOnboard` / [[../providers/CliSignInPanel]] /
  [[../providers/CustomEndpointForm]]）。本文件不再拥有任何一种"添加
  provider"的具体表单逻辑或本地 state。
- `CreateAgentPage.tsx` 独立地消费同一批三个组件（`OneKeyOnboard`——这个本
  来就是共享组件,没被这轮任务碰;加上这轮任务抽出的
  `CustomEndpointForm`/`CliSignInPanel`）做自己的 Step 1,不经过
  `ProviderSettings.tsx`——两个页面之间没有相互 import 关系,只是各自 import
  同一批 `components/providers/` 下的组件 + 共享的
  [[../providers/providerApi]] 网络层,符合本仓库"模块之间不互相依赖,靠共享
  组件复用"的约束。

## 2026-08-24 (3) — oauth tab 改为渲染 CliSignInPanel，删掉内联 Claude/Codex 登录卡

Add-provider 弹窗的 "oauth" tab（Claude Code Login 卡 + Codex CLI Login 卡整
段）不再内联渲染，改成：

```tsx
{addMethod === 'oauth' && (
  <CliSignInPanel providers={providerList} onComplete={refreshConfig} />
)}
```

`CliSignInPanel`（`components/providers/CliSignInPanel.tsx`，上一个任务抽
出）是自包含组件——自己的 claudeStatus/codexStatus/setupToken 等 state，自
己的登录/登出/setup-token handler，自己通过 `providerApi.ts` 的
`addProvider`/`fetchClaudeStatus`/`fetchCodexStatus` 打后端，自己的错误展
示；只需要 `providers`（复用本文件已有的 `providerList = Object.values
(providers)`，只用到其中的 `source`/`auth_type`）和一个 `onComplete` 回调。

一并删除了只为驱动旧内联 oauth 卡片存在的死代码：
- state：`claudeStatus` `claudeLoggingIn` `claudeLoggingOut` `codexStatus`
  `claudeLoginRemaining` `setupToken` `savingSetupToken`
- 倒计时 `useEffect`（每秒递减 `claudeLoginRemaining`，归零调
  `cancelClaudeLogin()`）
- handler：`handleAddClaudeOAuth` `handleSaveSetupToken`
  `handleAddCodexOAuth` `handleClaudeLogin` `handleClaudeLogout`
- 模块级 helper：`formatCountdown` `formatExpiresAt`（只被删掉的 JSX 用到）
- 模块级常量 `CLAUDE_LOGIN_TIMEOUT_SEC`（只被 `handleClaudeLogin` 用到，随
  之一起删）
- 派生变量：`claudeCard` `hasClaude` `claudeTokenConnected` `hasCodex`
  （现在完全在 `CliSignInPanel` 内部从它的 `providers` prop 派生）

**`error`/`setError`（组件级顶层 state）+ 顶层 `addProvider` 辅助函数（本
文件的 POST helper，非 `providerApi.ts` 那个同名导出）这次终于删干净
了。** 上一条目（08-24 (2)）里它俩被保留，理由是 `handleAddClaudeOAuth` /
`handleSaveSetupToken` / `handleAddCodexOAuth` 这三个 oauth handler 还在
调用 `addProvider`。本次把这三个 handler 整体删除后，逐个 grep 全文件确
认：`addProvider` 再没有任何调用点，顶层 `error`/`setError` 也再没有任何
读写点（弹窗底部 `{error && <p>...}` 那行随之一起删）——两者都是彻底死代
码，本次一起删除。

`refreshConfig` 同步简化：之前用 `Promise.all` 并发三个请求（providers 列
表 + `/claude-status` + `/codex-status`），现在只剩 providers 列表一个请
求——claude/codex 状态查询完全移进了 `CliSignInPanel` 自己的
`refreshStatuses`（mount 时调一次，登录/登出/add 之后各自重调）。

`@/lib/tauri` 的 import 行（`isTauri` `triggerClaudeLogin`
`triggerClaudeLogout` `cancelClaudeLogin`）整行删除——这四个只被刚删掉的
oauth handler/JSX 用到，现在都活在 `CliSignInPanel.tsx` 里。

验证：`tsc -b --noEmit`（0 条 ProviderSettings 相关报错）、`eslint`（0
报错/警告，含未使用变量检查，证明删除是完整的）、
`zh-localization.test.ts`（3/3 pass；该测试仍断言本文件不含
`'>Claude Code Login<'` 等字符串，断言仍然成立，但已经测不出什么了——留
给 Task 8 改成断言 `CliSignInPanel.tsx`）。

至此 `addMethod` 的三个 tab（onekey/oauth/custom）全部委托给独立组件
（`OneKeyOnboard`/`CliSignInPanel`/`CustomEndpointForm`），本文件不再内联
任何一种"添加 provider"的具体逻辑，只保留卡片网格、详情弹窗、编辑模型弹
窗和 tab 切换外壳。

## 2026-08-24 (2) — custom tab 改为渲染 CustomEndpointForm，删掉内联表单

Add-provider 弹窗的 "custom" tab（协议 select → name/auth_type/base_url/
api_key/models → test → submit 那整段）不再内联渲染，改成：

```tsx
{addMethod === 'custom' && <CustomEndpointForm onComplete={refreshConfig} />}
```

`CustomEndpointForm`（`components/providers/CustomEndpointForm.tsx`，上一个
任务抽出）是自包含组件——自己的 protocol/name/url/key/auth/models/testing
state，自己通过 `providerApi.ts` 的 `addProvider`/`testProviderConfig` 打
后端，只需要一个 `onComplete` 回调。

一并删除了只为驱动旧内联表单存在的死代码：
- state：`showForm` `formName` `formUrl` `formKey` `formAuth` `formModels`
  `formAdding` `formTesting` `formTestResult`
- handler：`handleAddProtocol`（POST 保存）、`handleTestForm`（POST
  `/test-config` 连通性探测）、`openForm`（协议切换时重置表单）

**`error`/`setError`（组件级 state）保留，没有删。** 逐个 grep 确认后发现
它不是 custom tab 专属——同一个 `addProvider()` 辅助函数（本文件的顶层
POST helper，非 `providerApi.ts` 那个同名导出）仍被 `handleAddClaudeOAuth`
/ `handleSaveSetupToken` / `handleAddCodexOAuth` 调用，这三个是 oauth tab
（Claude/Codex CLI 登录卡）的活代码，本次任务范围之外、留到后续任务再抽。
`addProvider` 内部的 `setError(...)` 调用因此仍是活的，`error` 的渲染
（`{error && <p ...>}`，弹窗底部，三个 tab 共用同一行）也保留原位不动。
只是 `handleAddProtocol`/`handleTestForm`/`openForm` 里那几处 `setError`
调用随函数整体删除——它们是 custom tab 专属的调用点，不是 `error` state
本身。

验证：`tsc -b --noEmit`（0 条 ProviderSettings 相关报错）、`eslint`（0
报错/警告，含未使用变量检查，证明删除是完整的）、
`zh-localization.test.ts`（3/3 pass，未改动该测试）。

## 2026-08-24 (1) — ModelBubbleInput / ModelSuggestionChips 搬到 providers/

提交 `22e59e21` 把 `ModelBubbleInput` 和 `ModelSuggestionChips` 原样搬出本文
件，落到新文件 `components/providers/ModelBubbleInput.tsx`
（[[ModelBubbleInput]]）。本次是机械抽取，不改行为、不改 props；本文件里
两处调用点（自定义端点表单 + 编辑模型弹窗）改成从新路径 import，其余渲染
逻辑不变。

动机：这是把"provider 连接"相关的可复用逻辑从本文件搬出去的第一步，为即将
新增的 Create Agent 向导 provider 步骤（会复用同一套 tag 风格 model 输入）
做准备，避免向导那边整段复制 ~120 行渲染代码。后续任务预计会继续把
`CustomEndpointForm` 也抽到 `components/providers/` 下。

## 2026-07-30 — OAuth 卡隐藏 Edit models

claude_oauth / codex_oauth 的模型列表是代码所有（读时覆盖），编辑是静默无效
操作——直接不渲染 Edit 按钮，别让用户改了个寂寞。

## 2026-07-26 — Claude 卡片 Section C：setup-token 粘贴连接

Claude Code Login 卡新增第三段（A=CLI 登录态、B=provider 记录态之下）：
`claude setup-token` 指引 + password 输入框 + 连接按钮，走既有
`addProvider({card_type:'claude_oauth', api_key: token})` 通道（后端对已
有卡做原位升级，槽绑定不动）。`auth_type === 'oauth_token'` 时 Section B
显示「已通过 setup-token 连接」，Section C 变为换 token 提示（一年期）。
推荐 token 而非登录态的原因 = 2026-07-23 macOS Keychain 命名空间事故：
token 运输层不依赖上方 CLI 登录状态。i18n 键 settings.provider.setupToken*
已加全 10 个 locale。

## 2026-07-23 — 自定义 provider 表单内「测试连通」按钮

原来只有详情弹窗（provider 存好后）才有 Test 按钮，添加表单只能保存。
现在自定义表单底部按钮改成一行两个：左「测试连通」（`handleTestForm`
→ `POST /api/providers/test-config`，直接测表单值、不落库）+ 右「Add
provider」。结果行 `formTestResult`（绿/红）复用详情弹窗测试结果样式；
`openForm`/切协议/保存成功都会清空它；**且 key/url/models/auth_type 任一
onChange 也清空**——测出红字后改正输入，红字不再挂着说谎。响应做兜底：
`.json().catch(()=>({}))` + `res.message || (string 型 res.detail) ||
networkError`，401/422 的 `{detail}` 不会渲染成一行空红字。i18n 新增
`settings.provider.testConnection`（10 语言）。

## 2026-07-21 — provider login and detail copy localized

The Claude Code and Codex CLI sign-in cards now keep product names but localize
the Login suffix, descriptions, status text, terminal guidance, and provider
record actions. Provider detail labels for endpoint, API key, and NetMind
account also resolve through the provider i18n namespace. Credential and
provider-management behavior is unchanged.

## 2026-07-16 — 每把 NetMind key 显示所属账户邮箱

`ProviderInfo` 加 `netmind_account_email?`;provider 详情在 API key 下方多渲染一行
"NetMind account: <email>"(仅当后端返回该字段时)。让用户分清几把 key 属于哪个账户、
充对账户(上游事故)。数据来自 GET /api/providers 的 `_attach_netmind_accounts` 增强。

> **Obsolete claim below (2026-07-10)**: the 2026-06-10 entry "accurate codex
> no-provider message" describes a `CODEX_ALLOWED_PROVIDER_SOURCES` filter and a
> "NetMind / Yunwu / OpenRouter not supported" message. That constant was
> **removed** (see [[agentFramework]] / [[user_service]] 2026-07-10) and
> that error copy no longer lives in this component (the agent-slot editor moved
> to [[ModelDefaultsSettings]] in #81). codex_cli now accepts any openai-protocol
> provider — aggregators included (binding rule #15).

## 2026-07-09 (latest) — card grid + detail/add modals; global default moved out

Final redesign of this component (supersedes the ordered-sections entry below):

- **Card grid**: each configured provider is a card (name + protocol/source badge
  + models count + masked key). Click → a **detail modal** (`detailProviderId`)
  showing protocol/source/auth_type, base_url, masked key, model chips, and the
  Test / Edit / Delete actions (reusing `handleTest` / `openEditModels` +
  edit-models dialog / `handleDelete`). The last grid cell is a dashed
  **"+ Add provider"** card → the **add modal** (`addModalOpen`).
- **Add modal** = **three tabs** (`addMethod` state: onekey / oauth / custom,
  default onekey) switched in place at the top of the modal — no wizard menu, no
  "paste an api key" step title. Tabs: **API key** (OneKeyOnboard preset dropdown
  + key), **Sign in** (Claude Code / Codex CLI login cards), **Custom** (the
  custom-endpoint form). `CUSTOM_PROVIDER_ENABLED` flipped
  back to `true` (Owner-authorized reversal of the 2026-06-17 hardening; the
  security note stays in the source, a custom base_url still routes agent traffic
  to a user-chosen host).
- **Global Default (old Section ③) is GONE from here** — moved to the new
  [[ModelDefaultsSettings]] under the "Model Defaults" nav item. All the slot /
  framework machinery was deleted: `SLOT_DEFS`, `renderSlotRow`,
  `getEffectiveSlotConfig`, `handleApply/Discard`, `handleLocalSlotChange/
  ReasoningChange`, the `agentFramework*` state, `slots` / `knownModels` /
  `officialBaseUrls` state, the getAgentFramework fetch, and the framework-only
  lib imports. refreshConfig now loads only providers (+ claude/codex status).
- The "Update available models" (sync) header action stays.

## 2026-07-09 — LLM Providers page: three ordered sections (list → add → default)

Since per-agent model/framework moved to chat, this page is a credential WALLET
+ a GLOBAL DEFAULT. It owns the whole vertical flow now (the old "Advanced"
junk-drawer disclosure at the [[SettingsPage]] level, the separate
ProviderSummaryCard, and the top-level one-key are all gone / folded in). Order,
top to bottom:

1. **① Your providers** — the configured provider list at the TOP (test / edit /
   delete). Empty state prompts to add below. Claude Code Login / Codex CLI Login
   ARE provider types: they appear here once added, and as sign-in options in ②.
   The **"Update available models" (sync)** action is a compact button in the ①
   section HEADER (right-aligned, via ``SectionHeader``'s new ``action`` slot),
   shown only when hasProviders: a Radix Tooltip explains it on hover, click runs
   ``handleSyncDefaults``; the sync result renders as a small line under the
   header. It's maintenance ON the existing providers, not an add action.
2. **② Add a provider** — OneKeyOnboard (primary paste-a-key, imported here now,
   ``onComplete=refreshConfig``), then the Claude Code / Codex CLI login cards
   and custom endpoints (feature-flagged off).
3. **③ Global Default** — the provider + model every agent INHERITS (was
   "Section 2 / Model Assignment", relabeled section2Title/Subtitle). Still writes
   user-level ``user_slots`` via the unchanged /api/providers endpoints; per-agent
   overrides live in chat ([[ComposerModelBadge]] + [[AgentLlmConfigPanel]]).

No collapse anymore — the sections are visible in order (an earlier iteration
folded provider-management into a ``showManage`` collapse; the Owner wanted the
list-first flow visible). ``SectionHeader``'s ``step`` badge is optional (no
numbered steps now). New i18n: providersListTitle/Subtitle, noProvidersYet,
addProviderTitle/Subtitle (en+zh; others fall back to en).

The framework list, codex curated models / allowed sources, recommended helper
models, model suggestions, and getModelsForSlot were extracted to
[[agentFramework]] and imported back (single source of truth shared with the
per-agent surfaces); SLOT_DEFS stays local.

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
``adapters.openai_agents._resolve_model`` mode 1).

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
providers.user_service.set_slot.

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

v1 source file (`adapters/codex/cli_sdk.py`) intentionally kept in the
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
- **`ModelBubbleInput` commit trap** — the component now lives in
  `components/providers/ModelBubbleInput.tsx` (moved by `22e59e21`); see
  [[ModelBubbleInput]] for the tag-input commit-trap gotcha (text typed but
  not committed via Enter / `+` is silently dropped on submit).

## 2026-07-07 (bug#3 跟进) — helper 下拉不再隐藏 OAuth provider

`renderSlotRow` 的 matching 过滤移除了 `helper_llm && auth_type==='oauth'` 排除项:订阅(claude_oauth/codex_oauth)现在可以进 helper 槽(后端经 CliHelperSDK 走 CLI 一次性)。SLOT_DEFS 的 helper 描述改为 'API key or subscription'。此前后端 auto-bind 已把槽绑好,但前端下拉过滤让 UI 显示 'No provider configured' —— 本次对齐。
