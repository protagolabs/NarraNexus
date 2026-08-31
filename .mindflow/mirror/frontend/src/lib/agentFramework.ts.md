---
code_file: frontend/src/lib/agentFramework.ts
last_verified: 2026-08-28
stub: false
---

## 2026-08-28 — 插件安装可用性合并（`frameworkAvailabilityMap` / `withFrameworkAvailability`）

轻量化插件化后 Claude Code / Codex CLI 从桌面镜像里移出，改成用户按需装的本地
插件（见 [[PluginsSettings]] / `backend/integrations/plugins`）。`GET/POST
/api/providers/agent-framework` 的响应新增可选 `frameworks: [{name, available}]`
——`frameworkAvailabilityMap()` 把它拆成 name→available 的查表，
`withFrameworkAvailability()` 把这张表贴到 `AgentFramework[]` 上（新增可选字段
`available`），`isFrameworkAvailable()` 判定。

三个函数刻意和 `availableFrameworks()`（钱包能不能驱动）分层：那个函数的语义是
**隐藏**死路选项；这里的语义是**禁用但仍列出**——插件没装的框架必须让用户看得
见、点得到"去装"，隐藏就等于把安装入口也一起藏没了。两个选择器
（[[ModelDefaultsSettings]] / [[AgentLlmConfigPanel]]）都是先调
`availableFrameworks()` 再套这层。

`frameworkAvailabilityMap` 对 `frameworks` 缺失的键（旧后端没有这个字段，或者
某个框架不是插件如 nexus_power）一律按"可用"处理——fail-closed 在这里是错的
方向：字段还没上线就把所有框架锁死，用户会以为整个功能坏了。
## 2026-08-28 — ProviderSummary 改为 providersApi.ProviderRow 的别名(PR #376 bot 轮)

本模块曾持有行形状的**第四份**结构副本,三个消费方
(AgentLlmConfigPanel / ComposerModelBadge / ModelDefaultsSettings)拿着
已经类型正确的 `api.getProviders()` 结果再 `as` 回这份窄副本——断言不是
转换,它关掉了三处的类型检查(后端上次加 netmind_account_email 这类字段
时就会静默漏掉)。现在 `export type ProviderSummary = ProviderRow`,三处
cast 删除。**import type 引 ProviderRow 是刻意的**:providersApi 运行时
import @/lib/api,值导入会造成 agentFramework → providersApi → api 运行时
环(api.ts 只做 type-only 反向引用才无环)。

## 2026-07-31 — `providerBacksFramework()` + `availableFrameworks()`

`providerBacksFramework(prov, framework)` 是后端
[[provider_schema]] `framework_can_drive_provider` 的孪生：protocol 门 +
订阅凭据门。两个 agent-slot provider 下拉从 `frameworkAcceptsProtocol` 换成
它——只查协议时，claude_oauth（anthropic）在 NexusPower 下照样可选，存得进
库，跑起来才炸。

`availableFrameworks(providers, current)` 决定框架下拉列什么：钱包里没有一张
卡能驱动的框架是死路（选中后 provider 下拉直接空），所以隐藏。两个刻意的
逃生口：**providers 为空不过滤**（还没加卡的用户不该面对空下拉）、
**current 永远保留**（丢掉选中值会让 `<select>` 悄悄改指向另一个框架）。

**它不查云端 staff-only 规则**（`frameworkAllowedInCloud`）：那两处选择器是
故意把云端禁用的框架继续列出来、选中时弹解释，比悄悄变短的列表友好。两条
规则问的是不同的问题，别合并。

## 2026-07-29 — `frameworkAllowedInCloud()`：后端云策略的前端孪生

新增 `CLOUD_ALLOWED_FRAMEWORKS` + `frameworkAllowedInCloud()`，对应后端
`cloud_policy.CLOUD_ALLOWED_FRAMEWORKS`。两个框架选择器（
[[ModelDefaultsSettings]] 与 [[AgentLlmConfigPanel]]）此前各自内联了
`!== 'claude_code'`，NexusPower 变成云端合法之后**两处都还在拒**——所以这里
导出的是**谓词**而不是常量，与 `isSlotBindableSource` 同样的教训。

## 2026-07-29 — 框架可接受协议从"一个"变成"一组"

新增可选 `protocols` 与 `frameworkAcceptsProtocol()`:CLI 型框架天生只会一种
协议(claude_code→anthropic、codex_cli→openai,因为底下的 CLI 只会一种),而
NexusPower 直接驱动 provider API,两种都行,**不能**被过滤成一种。UI 上叫
`NexusPower-beta`。另有 `isNexusPowerFramework()` 谓词,把散落的
`=== 'nexus_power'` 收成一处(与既有 `isCodexFramework` 同型)。

## 2026-07-17 — 新增 cloudNetmindOnly 策略谓词 + DESKTOP_RELEASES_URL

`cloudNetmindOnly(role)` = `isForcedCloud() && role !== 'staff'`——后端两个
route 门禁（providers.py / agents/llm_config.py 的云端 netmind-only 槽位策略）
的前端孪生。两个槽位编辑器（[[ModelDefaultsSettings]] +
[[AgentLlmConfigPanel]]）都经它过滤 provider 下拉（`source !== 'netmind'`
隐藏），保证 UI 不给出会被 403 的选项。role 由调用方从 configStore 读出传入
（本 lib 保持纯函数，不 import store）。注意与铁律 #15 的区别：这不是"平台
判断 provider 合不合适"，而是云端商业策略（自有 key = 本地版功能）。
`DESKTOP_RELEASES_URL`（NetMindAI-Open releases 页）随之导出，供"下载本地版"
提示链接复用。本文件首次 import runtimeConfig（isForcedCloud）。

## 2026-07-10 — 删 CODEX_ALLOWED_PROVIDER_SOURCES + curated 收窄到 codex_oauth

`CODEX_ALLOWED_PROVIDER_SOURCES` 常量**已删除**。它以前是 codex_cli agent slot 的
source 白名单(`{codex_oauth, user}`),配合后端 `validate_slot_binding` 把 NetMind /
Yunwu / OpenRouter 挡在外面。按铁律 #15(平台不替用户判断 provider 是否合适)整条
移除,恢复 pre-#81 行为——codex agent slot 现在只查 protocol。调用它的两处过滤
([[AgentLlmConfigPanel]] / ModelDefaultsSettings)同步删掉 source 分支,只留
`p.protocol !== fw.protocol`。

`getModelsForSlot` 的 codex 分支**收窄到 `prov.source === 'codex_oauth'`**:只有
OpenAI 自己的 codex 后端才强制 `CODEX_CURATED_MODELS`(它按账号 tier 网关);其他
openai provider(聚合商/自填 base_url)返回自己的 `prov.models`。与后端
`get_user_service.get_user_config`(同样 codex_oauth-only 覆盖)对齐。修掉"选了
netmind 却只看得到 gpt-5.x 三个模型"的 bug。详见后端 mirror
[[user_service]] 2026-07-10 条目。

## 2026-07-09 — defaultHelperModel(选 helper provider 时默认便宜模型)

新增 `defaultHelperModel(source, protocol, modelIds)`:helper slot 选定 provider 后,
默认挑**推荐的便宜模型**而非 `models[0]`(旗舰)。优先 `RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`
(openai→gpt-5.4-mini / anthropic→claude-haiku-4-5);OAuth provider 列的是 CLI 别名、
具体推荐 id 可能不在列表里,故映射到后端 auto-bind 同款别名(claude_oauth→`haiku`、
codex_oauth→`gpt-5.4-mini`),都不在时才回退首个。`ModelDefaultsSettings` 与
`AgentLlmConfigPanel` 共用,修掉"选 codex 后 helper 默认成 gpt-5.5 旗舰 / 选 claude 后默认
opus"的问题。

## 2026-07-09 — shared framework/model helpers for the provider UI

Single source of truth for the LLM provider/slot UI, shared by the user-level
Settings editor ([[ProviderSettings]]) and the per-agent chat surfaces
([[ComposerModelBadge]], [[AgentLlmConfigPanel]]) so a per-agent override offers
exactly the same choices as the global-default editor.

Holds: ``AGENT_FRAMEWORKS`` + ``isCodexFramework``; ``CODEX_CURATED_MODELS``
(codex_oauth-only — mirror of backend ``providers.user_service``);
``RECOMMENDED_HELPER_MODEL_BY_PROTOCOL`` (mirror of backend
``_ONBOARD_HELPER_MODELS``); ``MODEL_SUGGESTION_GROUPS``; reasoning option
lists; and ``getModelsForSlot(prov, slot, framework, knownModels)`` (agent+codex
**on codex_oauth** → curated set, every other provider → its own models). These
were previously local to ProviderSettings; extracting them avoided duplicating
the codex rules across the new per-agent components. (The old
``CODEX_ALLOWED_PROVIDER_SOURCES`` source-allowlist was removed 2026-07-10 — see
the dated entry above.)
