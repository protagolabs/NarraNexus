# Docker Cloud Agent — 接入 Manyfold 设计方案

- **日期**: 2026-05-22
- **分支**: `feat/docker_cloud_agent`
- **背景**: 2026-05-18 与 Ying 的会议纪要（Manyfold 平台 ↔ NarraNexus 适配协作）
- **命名**: 平台品牌 "Manifold AI"，但其代码包命名空间是 `@manyfold/shared`。本文及代码标识符统一用 **`manyfold`**（`WorkingSource.MANYFOLD` / `ENABLE_MANYFOLD_API` / `/manyfold/agents`），与平台代码 id 对齐。
- **状态**: 设计敲定（Owner 终审完成 2026-05-25），进入 TDD 实现阶段

> **一句话**：把 local 版 NarraNexus 整个装进一个 Docker（多 user、多 agent），Manyfold 平台一边把容器 `:8000` 的 **native UI 映射出去**给用户用，一边通过 **OpenAI 协议的 `/v1/chat/completions`** 程序化和 agent 对话——后者内部复用「channel/trigger」机制（消息透传、用 creator 身份、标 `manyfold` channel、`reply_to_manyfold` tool 回复）。这套只在给 Manyfold 部署的镜像里启用，本地版/云端版不受影响。

---

# Part 1 · Manyfold 怎么利用一个 agent

> 这部分讲清楚：Manyfold 是什么、它怎么对待一个 agent runtime、它期望一个 runtime 提供哪些能力。读完就知道我们要对齐的"靶子"长什么样。来源：`reference/netmind-cloud-agents` 仓。

## 1.1 Manyfold 是什么

一个**云端 agent 编排平台**（monorepo `netmind-cloud-agents`，品牌 Manifold AI）。用户登录后选 business mode → 选部署环境 → 选 runtime 框架 → 配 model provider → 建 agent → 进 agent 的 native UI / 对话。已接入 7 种 runtime 框架：`openclaw`、`hermes`、`claude-code`、`codex`、`gemini-cli`、`dify`、`langflow`。**`narranexus` 已有占位但 `disabled: true`（标 "planned"）**（`apps/web/src/lib/agentCreate/frameworkOptions.ts:109-113`）——我们要做的就是把它点亮。

**三种部署环境**（一个 runtime 声明自己支持哪几种）：
- **Sandbox**：按量计费，便宜，支持类型有限。
- **K8S**：每个 agent 拉一个容器/pod，适合要后台任务的场景。
- **Local Daemon**：注册用户自己的电脑（重点方向，免订阅费）。

## 1.2 平台怎么对待一个 agent（生命周期）

以 K8S 环境的 `openclaw` 为样例（`apps/api/src/modules/agents/orchestration/k8s-agent-orchestrator.ts`）：

1. **建**：用户建 agent → 平台调该 framework 的 `bootstrap.plan()` 生成部署计划（端口、env Secret、就绪探针、资源）→ apply K8s Deployment/Service/Ingress。
2. **起**：容器启动，从注入的 env 读 gateway token / LLM 配置，监听 `0.0.0.0:{port}`。
3. **就绪判定**：平台轮询 K8s `availableReplicas>=1` + Ingress 有地址 + `GET {ingressHost}/healthz` 返回 200，才宣告 agent ready（`k8s-agent-orchestrator.ts:758-798`）。
4. **对话**：用户消息 → 平台 `ApiChatAdapter.sendMessage()` → 先 `HEAD /` preflight → `POST {ingressHost}/v1/chat/completions`（`Authorization: Bearer {token}`，`stream:true`）→ 解析 SSE 流式回前端（`chat/adapters/openclaw.adapter.ts:90-328`）。
5. **管理**：health-check / diagnostics / storage-usage / 删除。

> 对 NarraNexus（Owner 定调）：**不做 per-agent pod**，而是一个容器 = 一个多 user/多 agent 的"本地版实例"，平台把它当**实例级 runtime** 注册。对话按 agent_id 路由（见 Part 2）。

## 1.3 平台期望一个 runtime 提供的能力全集

平台对**所有 7 个框架**约定了统一接口，这就是"一个 agent 该有哪些功能"的全集。按维度列出（这是 Owner 要的"共性基本功能清单"）：

**A. 对话类（核心）** — `ApiChatAdapter`（`apps/api/src/modules/chat/chat-adapter.ts:77-84`）
- `sendMessage()` 流式对话，事件类型：`token` / `tool_call` / `tool_result` / `thinking` / `usage` / `error` / `done` / `raw_source`（:31-84）
- OpenAI 兼容端点 `POST /v1/chat/completions`（`openai-chat-completions.controller.ts`）
- 取消进行中请求 `POST sessions/:id/cancel`（`chat.controller.ts:116`）
- 能力声明 `getCapabilities()`（:80）

**B. 会话与历史类** — `chat.controller.ts`
- session 列/建/改/删（:46-80）
- 会话消息历史 `GET sessions/:id/messages`（:98）
- 重新生成某条消息 `regenerate`（:155）
- SSE 流订阅 `GET sessions/:id/stream`（:180）

**C. 生命周期类** — `K8sFrameworkBootstrap`（`bootstrap/k8s-framework-bootstrap.ts:79-83`）
- `plan()` 部署计划（端口/env/探针/资源）
- `readinessProbe` / `httpReadinessPath` 就绪探针
- `postProvision?()` 启动后钩子

**D. Agent CRUD + 可观测类** — `agents.controller.ts`
- 列 / 建 / 删 / 详情 / 改元数据（:73-182）
- `health-check`（:242）/ `diagnostics`（:252）/ `storage-usage`（:231）

**E. 模型与凭据配置类** — `agents.controller.ts`
- model-config 读写（:193-203）/ refresh-models（:214）
- credentials 读 / 明文 / 改（:264-284）

**F. 文件类** — `files/files.controller.ts:53-200`（9 个端点）
- files-session token / roots / list / stat / read / write / mkdir / mv / rm

## 1.4 容器接入契约（一个 runtime 容器必须满足）

| 契约项 | 要求 | 来源 |
|--------|------|------|
| 绑定 | 监听 `0.0.0.0:{port}`（不能只 127.0.0.1）| `docker/openclaw/entrypoint.sh:42-75` |
| 端口 | 单端口，bootstrap `plan()` 声明 | `bootstrap/openclaw.ts:30-46` |
| 就绪探针 | `GET /healthz` → 200 | `bootstrap/openclaw.ts:53-58` |
| preflight | `HEAD /` → 任意 200（5s 超时）| `openclaw.adapter.ts:175` |
| 对话 | `POST /v1/chat/completions`，OpenAI 兼容，`stream:true`，SSE（`data:{...}` … `data:[DONE]`）| `openclaw.adapter.ts:90-328` |
| 鉴权 | `Authorization: Bearer {token}`，token 平台启动时注入 env | `openclaw.adapter.ts` |
| env 注入 | K8s Secret：gateway token、LLM model/provider/key/base_url、workspace 等 | `bootstrap/openclaw.ts:30-46` |
| 平台接入需改 | ①实现 bootstrap 类 ②去 `disabled` ③出排除名单 ④配 `K8S_IMAGE_NARRANEXUS` | `frameworkOptions.ts`、`k8s-agent-orchestrator.ts:742-755` |

---

# Part 2 · 我们要加入 Manyfold 得做哪些事

## 2.1 总体形态

- **一个容器 = 一个"本地版" NarraNexus 实例**：多 user、多 agent，行为和 `bash run.sh` 一致（守铁律 #7）。默认 **SQLite + volume**（自包含），保留 MySQL（`DATABASE_URL=mysql://` 去掉 sqlite_proxy）给云端多副本。
- **两条交互路径并存**：
  1. **native UI 映射（核心诉求）**：容器 `0.0.0.0:8000` 同端口 serve native UI（已有 StaticFiles+SPA，`backend/main.py:330-343`）+ API；平台 ingress/iframe 把它 proxy 出去，用户在我们界面里做细粒度操作和对话。
  2. **OpenAI 协议程序化对话**：`POST /v1/chat/completions`（OpenAI 输入/输出，SSE）= **外壳 + transport**；内部复用 channel 机制跑出结果，`reply_to_manyfold` 翻译回 SSE = **内部管道**。详见 2.4。
- **deployment-gated**：上述 OpenAI 端点 + 跨 user 列表，**只在给 Manyfold 的镜像里启用**（`ENABLE_MANYFOLD_API=1`），本地版/云端版不注册这些端点。

## 2.2 工作项分期

> 用结构性维度描述大小（守铁律 #17），不估时。

### Phase 1 — 容器化（主体）
- `Dockerfile`（多阶段：node build 前端 → python3.13 运行时 + `@anthropic-ai/claude-code` CLI）、`entrypoint.sh`（起必须进程；按 `DATABASE_URL` 决定是否起 sqlite_proxy）、`docker-compose.yml`、`.dockerignore`
- 所有 `~` 硬编码路径 env 化、`0.0.0.0` 绑定、镜像内**不带** `.env`（让平台注入 env 生效）
- 验收：`docker run` + 挂 volume → native UI 在 8000 可访问 → 注册多 user、各建 agent、UI 里跑一轮 chat → 重启数据还在
- 风险：claude CLI 无 TTY 鉴权（需注入 `ANTHROPIC_API_KEY` 或 provider key）

### Phase 2 — OpenAI chat endpoint（程序化对话，详见 4.4/4.5）
- 新增 `backend/routes/openai_compat.py`：`POST /v1/chat/completions`，取 `messages` 末条 + `model`(**= agent_id**，平台必须把目标 agent_id 放在 OpenAI 标准 `model` 字段里) → 解析 → `_resolve_agent_owner` 拿 creator → 进程内驱动 `BackgroundRun`（`working_source=MANYFOLD` + manyfold 标记）→ user-facing 回复事件翻译成 OpenAI SSE chunk
- **错误响应也要回填 `model` 字段**（OpenAI 标准 error envelope 里的 `model` 字段填请求里收到的 agent_id 原值，不要返回空或框架占位符）
- `WorkingSource.MANYFOLD` 枚举；`ENABLE_MANYFOLD_API` 门控 endpoint + module 注册（不是进程）
- 轻量 `module/manyfold_module/`：`reply_to_manyfold` MCP tool + 最小 Module + ContextBuilder（MCP 7833）+ MessageSourceRegistry 注册
- **无独立 trigger 进程、无新表、不继承 ChannelTriggerBase**
- 新增/改的 `.py` 同 commit 配 mirror md（守铁律 #10）
- 验收：`ENABLE_MANYFOLD_API=1` 起容器 → curl 以 OpenAI 格式 POST 一条（`model: "<agent_id>"`）→ agent 以 creator 身份跑、标 manyfold、用 `reply_to_manyfold` 回复 → SSE 流式 + `[DONE]`；不设该 env 时端点 404、本地/云端行为零变化；非法 agent_id 错误响应 `error.message` 清晰 + `model` 字段回填请求值

### Phase 3 — 平台对接面 + 文档
- `GET /healthz`、确认 `HEAD /`、deployment-gated `GET /manyfold/agents`（跨 user）、`backend/auth.py` 加 gateway-token 兜底鉴权
- native UI 在反代/子路径下可用（base path、相对 API、WS 经反代）、CORS 放开平台域
- 给 `netmind-cloud-agents` 的接入说明：narranexus runtime 注册（去 `disabled`）、注入哪些 env、`K8S_IMAGE_NARRANEXUS`、native-UI proxy 方式、OpenAI 端点/鉴权契约、readiness 打 `/healthz`
- 给 Ying 的 action items：workspace 路径规则 `{agent_id}_{user_id}`（注意顺序）、skill 目录 `skills/<name>/SKILL.md`

## 2.3 涉及的层

> **改动归属说明**：每行末尾用 `[#1/#2/#3]` 标注工作分类——`#1` = 项目自身改进（通用收益）、`#2` = Docker 打包、`#3` = Manyfold API + bridge。详细文件清单见 **Part 5**。

| 层 | 变更 | 归属 |
|----|------|------|
| 打包 | **一份** `Dockerfile`（多阶段，复用 `scripts/run.sh` 作为 CMD）；`.dockerignore`。**无独立 entrypoint.sh / 无 docker-compose.yml**——entrypoint 逻辑融进经容器化改造的 `scripts/run.sh`（守铁律 #7） | #2 |
| Schema | `WorkingSource` 加 `MANYFOLD`；`MANYFOLD_GATEWAY_TOKEN`/`ENABLE_MANYFOLD_API` env（不入库）。**无新表**、无破坏性表变更（守铁律 #6）| #3 |
| Manyfold API 层 | `backend/routes/openai_compat.py`：`POST /v1/chat/completions`；`backend/routes/manyfold_agents.py`：`GET /manyfold/agents`；`backend/main.py` 加 `GET /healthz` + 条件 router 注册；`auth.py` 加 `MANYFOLD_GATEWAY_TOKEN` 模式 + URL fragment `#token=` 兜底。全部 `ENABLE_MANYFOLD_API` 门控注册 | #3 |
| Manyfold Module | `module/manyfold_module/`：`reply_to_manyfold` MCP tool + 最小 Module + ContextBuilder（MCP 7833）；MODULE_MAP + MessageSourceRegistry 注册。**无 trigger / 无 credential 表 / 不继承 ChannelTriggerBase** | #3 |
| Claude 凭证管理（新统一 UX）| **新增** `backend/routes/claude_auth.py`（Option 1 粘 token / Option 2 交互登录 endpoints）；前端 `ProviderSettings.tsx` 重写（去掉 `isTauri()` 门控，加 Option 1/2 UI）；**删除** Tauri Rust `commands/auth.rs` 里的 `trigger/cancel/get_claude_login_status`（守铁律 #2，不留兼容层）。**全模式统一走 backend HTTP**。详见 Part 4.12 | #1 |
| 配置 | `settings.py`/`config.py`：路径全 env 化、`0.0.0.0`、镜像内不带 `.env`、新 env（`ENABLE_MANYFOLD_API` / `MANYFOLD_GATEWAY_TOKEN`）| #2（路径 env 化、绑 0.0.0.0）+ #3（Manyfold env）|
| 前端 | native UI 在反代/子路径可用、CORS；首次加载捕获 URL fragment `#token=` 并把它当 Authorization 用 | #1（反代/子路径/CORS）+ #3（URL fragment 捕获）|
| 启动脚本 | `scripts/run.sh` 改造成容器友好：env 检测、`0.0.0.0` 绑定、`/data/*` 路径默认（守铁律 #7：dmg/local/容器同一脚本）；`dev-local.sh` 不变 | #1（脚本通用改进）|
| 编排层 | **不动** agent_runtime / agent_loop（守铁律 #14/#15）；endpoint 复用 `BackgroundRun` | — |
| 文档 | `deploy/README` + 给 Ying 的接入契约说明 | #3 |

## 2.4 chat = OpenAI endpoint（不要独立 trigger）

**核心**：chat 是我们给 Manyfold 的 API 集里**多一个 endpoint** `POST /v1/chat/completions`，在 backend 进程内驱动一次 agent run，把 manyfold 当成一个 **channel 来源标记**——而不是一个独立 trigger 进程。详见 Part 4.0 / 4.4 / 4.5。

**保留的 channel 语义**（来自现有机制）：
- **user = creator**：复用 `_resolve_agent_owner(agent_id)` 查 `agents.created_by`（`channel_trigger_base.py:918-930`，可直接调用其逻辑，不必继承整个 ABC）。
- **channel 标记**：run 上打 `manyfold` 来源（`WorkingSource.MANYFOLD` + ChannelTag），agent / narrative 知道消息来自 manyfold。
- **透传 + 语义自处理**：消息走和 native chat 同一条 `BackgroundRun` 路径，narrative 选择交给 agent。

**砍掉**（相对 IM 模块）：`ChannelTriggerBase` 子类、trigger 进程、`connect()` 轮询、credential watcher、`channel_manyfold_credentials` 表——同步请求/响应用不上。

**回复（✅ 专属 tool，附录 B.5）**：agent 用专属 `reply_to_manyfold` MCP tool 显式回复；endpoint 抓该 tool 输出翻成 OpenAI SSE（详见 4.5）。

**最小新建/修改**：
- `schema/hook_schema.py`：`WorkingSource` 加 `MANYFOLD`。
- `backend/routes/openai_compat.py`（新）：`/v1/chat/completions`，deployment-gated 注册。
- 轻量 `module/manyfold_module/`：`reply_to_manyfold` MCP tool + 最小 Module + ContextBuilder（MCP 端口 7833）；MODULE_MAP + `MessageSourceRegistry` 注册。
- **无新表、无新进程、无启动脚本改动**（不起 trigger、不继承 ChannelTriggerBase）。

**部署开关 `ENABLE_MANYFOLD_API`**：仅该 env 存在时，backend 注册 openai_compat router + 跨 user 端点（+ 可选 reply-tool module）。门控的是**端点注册**，不是进程。本地版/云端版不设 → 端点 404、行为零变化。

---

# Part 3 · 功能满足情况（gap 分析）

> 对照 Part 1.3 的能力全集，逐项标 NarraNexus 现状（✅满足 / 🟡部分 / ✗缺）、怎么补、v1 做不做。

## 3.1 逐能力对照

### A. 对话类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| 流式对话（token/tool_call/thinking/...）| ✅ WS BackgroundRun 已有等价事件 | 翻译成 OpenAI SSE | **做** |
| `POST /v1/chat/completions` | ✗ | 新建端点（壳）+ channel（内核）| **做** |
| 取消进行中请求 | ✅ WS `{action:"stop"}` | OpenAI 端 abort 映射 | 可选 |
| `getCapabilities()` | ✗ | 静态声明 | 可选 |

### B. 会话与历史类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| session 列/建/改/删 | ✗ 无显式 session（用 agent+narrative）| —— | 暂不做 |
| 会话消息历史 | ✅ `simple-chat-history`/`chat-history`/`event-log` | native UI 已覆盖 | native UI |
| 重新生成 | ✗ | —— | 暂不做 |
| SSE 流订阅 | ✅ WS 重连回放 | native UI 已覆盖 | native UI |

### C. 生命周期类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| `plan()` 部署计划 | 平台侧实现 | Phase 3 文档协助 | 文档 |
| 就绪探针 | ✗ | 加 `GET /healthz` | **做** |
| `postProvision()` | —— | 可选 | 暂不做 |

### D. Agent CRUD + 可观测类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| 列 agent | ✅ `/api/auth/agents`（per-user）| 加**跨 user**变体 `/manyfold/agents` | **做（跨 user）** |
| 建/删/改/详情 | ✅ `/api/auth/agents` CRUD | native UI 已覆盖 | native UI |
| health-check | 🟡 | `/healthz` 覆盖 | **做（healthz）** |
| diagnostics | ✗ | —— | 暂不做 |
| storage-usage | 🟡 artifacts quota | —— | 暂不做 |

### E. 模型与凭据配置类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| model-config 读写 | ✅ `/api/providers/slots` | native UI 已覆盖 | native UI |
| refresh-models | ✅ `/api/providers/catalog` | native UI 已覆盖 | native UI |
| credentials 读/明文/改 | ✅ `/api/providers`（mask）| native UI 已覆盖 | native UI |

### F. 文件类
| 能力 | 现状 | 补法 | v1 |
|------|------|------|----|
| token/roots/list/stat/read/write/mkdir/mv/rm（9 端点）| ✗ 无通用文件 CRUD（有 attachment/artifacts）| native UI 可后补 | 暂不做 |

## 3.2 v1 选型结论

- **要做（4 项）**：① OpenAI-compat `/v1/chat/completions`（壳）+ manyfold channel module（内核）② 跨 user 列 agent ③ `GET /healthz` 就绪 ④ native UI 可反代。
- **靠 native UI 覆盖、不单独做 API**：会话历史、模型/凭据配置、agent 详情 CRUD。
- **暂不做**：session CRUD、regenerate、diagnostics、storage-usage、文件读写 API。

**结论一句话**：我们对话类基本满足（差一层 OpenAI 壳），配置/历史/CRUD 类已被 native UI 覆盖（这正是"native UI 映射出去"的价值），缺的是 session 抽象、文件 API、诊断——这些 v1 不做。

---

# Part 4 · 详细技术方案（每个环节怎么做）

> 这部分是实现蓝图，逐环节给出**具体做法**。实现遵循 TDD：每个环节先写测试再写实现（守铁律 + Superpowers TDD）。

## 4.0 关键决策：chat 就是 API 里多一个 endpoint（不要独立 trigger）✅ Owner 已定

**Owner 定调**：我们本来就要给 Manyfold 提供一套 API，chat 不过是其中**多一个 endpoint**。不要独立 trigger 进程，也不要 `ChannelTriggerBase` 那套轮询/credential/dedup 机制——那是为"平台推送、长连接"设计的，和 OpenAI 的**同步请求/响应**不搭。

**最终形态**：`POST /v1/chat/completions` 这个 endpoint（在 backend 进程内）：
1. 解析 OpenAI 请求 → agent_id + 末条 user 输入。
2. `_resolve_agent_owner(agent_id)` 拿 creator user_id。
3. 以 `working_source=MANYFOLD`、带 `manyfold` channel 标记，**在进程内直接驱动 `BackgroundRun`**（复用 `websocket.py` 那套，已验证）。
4. agent 的 user-facing 回复事件就地翻译成 OpenAI SSE chunk 同步还回。

**这仍然"把 manyfold 当一个 channel"**——保留的是 channel 的**语义**（消息透传、creator 身份、`manyfold` 来源标记、记忆持久化），丢掉的只是**传输机制**（轮询进程）。因为传输是请求/响应，入口天然就是 HTTP endpoint。

**因此砍掉**：`ChannelTriggerBase` 子类、`manyfold_trigger.py`、`run_manyfold_trigger.py`、`connect()` 轮询、credential watcher、`channel_manyfold_credentials` 表（平台直接传 agent_id + gateway token，无 per-agent IM 凭据要存）。
**保留的"channel 风味"件**：`WorkingSource.MANYFOLD` 枚举、run 上的 `manyfold` channel 标记、专属 `reply_to_manyfold` tool（轻量 module，见 4.5）。
**`ENABLE_MANYFOLD_TRIGGER`（更名 `ENABLE_MANYFOLD_API`）** 门控的是 **endpoint + module 注册**（openai_compat router + 跨 user 端点 + manyfold module），**不是进程**。

## 4.1 Dockerfile（多阶段，单文件）
**约束**（Owner 定调）：本次新增**只有一个 `Dockerfile`**——`docker/manyfold/Dockerfile`。容器内启动逻辑**不写独立 entrypoint.sh**，而是改造现有 `scripts/run.sh` 让它容器友好（env 检测、`0.0.0.0` 绑定、`/data/*` 路径默认），`Dockerfile` 的 `CMD` 直接指向 `scripts/run.sh`。守铁律 #7：dmg sidecar / 本地 `bash run.sh` / Manyfold 容器**共用同一份 `run.sh`**。

- **Stage 1（前端 build）**：`node:22` → `npm ci` + `npm run build`（`frontend/`）→ 产出 `frontend/dist`。
- **Stage 2（运行时）**：`python:3.13-slim`（或 openclaw 同款 `debian-bookworm` 便于平台统一）→ 装 `uv`、Node22（claude CLI 需要）、`npm i -g @anthropic-ai/claude-code` → `uv sync` 装 Python 依赖 → 拷源码 + Stage 1 的 `frontend/dist`。
- `EXPOSE 8000`；`ENV` 默认值把所有 `~` 路径指到 `/data/...`（volume 挂载点）。
- `CMD ["bash", "scripts/run.sh"]`。
- **持久化 volume**：`/data`（DB + workspaces + logs）+ `/home/app/.claude/`（Claude credentials，**关键**：Option 2 交互登录写入的 `.credentials.json` 必须跨容器重启存活，否则每次重启都要重新登录）。
- `.dockerignore`：排 `.git`、`.venv`、`node_modules`、`reference/`、`drafts/`、`*.db`、`.env`（**关键：镜像绝不带 .env**）。

> **8000 端口在不同部署模式下跑的是什么——三句话注脚**：
> - **本节描述的"容器内 8000"**：单进程 uvicorn FastAPI 通过 `StaticFiles` + SPA fallback **同端口同时 serve 前端 dist + API + `/v1/chat/completions` + `/healthz`**（守铁律 #7 和 `bash run.sh` 生产 / Tauri sidecar 一致）。
> - **本地开发模式不一样**：`make dev-frontend` 起 Vite **:5173**（HMR），`make dev-backend` 起 uvicorn **:8000**（只 API，无前端 mount），Vite proxy `/api/*` `/ws/*` 到 8000。**镜像内不存在 :5173**。
> - **当前 EC2 生产部署也不一样**（参考 `stacks/narranexus-app/compose.yml`）：分前端 nginx 容器（内部 :80）+ backend 容器（内部 :8000，**只 API**），最前面 Caddy 终结 TLS——**与 Manyfold 单容器架构不同**，因为 EC2 是长生命周期生产实例，分容器有运维好处；Manyfold 是 per-runtime pod + 单端口约束，必须合容器。完整对比见附录 A.1。

## 4.2 进程编排（统一 `scripts/run.sh`，不新增 entrypoint.sh）
**Owner 定调**：不写 `docker/manyfold/entrypoint.sh`——把容器场景需要的能力**全部融入现有 `scripts/run.sh`**，让 dmg sidecar / 本地 / 容器三种场景共用一份脚本（守铁律 #7）。Dockerfile 的 `CMD` 直接调它。

- `run.sh` 读 env 决定拓扑：`DATABASE_URL` sqlite→起 sqlite_proxy（等 :8100 ready），mysql→跳过。
- 顺序：sqlite_proxy（若需）→ 等就绪 → 起 MCP / Poller / JobTrigger / BusTrigger → 起 backend（uvicorn `--host 0.0.0.0 --port 8000`）。
- 新增 env 检测分支（容器模式）：
  - 检测到 `ENABLE_MANYFOLD_API=1` → backend 注册 manyfold routers
  - 检测到 `RUNTIME_MODE=container`（或 `IN_CONTAINER=1`，按实现选）→ 走 `/data/*` 默认路径、`0.0.0.0` 绑定、日志全 stdout
  - 不设这些 env → 行为同当前 dmg / 本地（守铁律 #7 反向兼容）
- `auto_migrate()` 在进程启动时幂等建表（已有机制）。
- 前台 hold backend（容器主进程），其余 worker 后台 + 日志汇到 stdout（平台收集）。
- `dev-local.sh` 不动（开发独立场景）。

## 4.3 路径 env 化
- 审 `settings.py:142-149` + `logging/_setup.py:36` + sqlite 默认路径，确保都能被 env 覆盖（`BASE_WORKING_PATH` 已支持；其余补齐 env 读取）。
- entrypoint 设：`BASE_WORKING_PATH=/data/workspaces`、`NEXUS_LOG_DIR=/data/logs`、narrative/trajectory→`/data/...`、`DATABASE_URL=sqlite:////data/nexus.db`。
- volume：`/data` 一个挂载点装全部持久化（db + workspaces + logs）。

## 4.4 OpenAI `/v1/chat/completions` 端点
- 新 router `backend/routes/openai_compat.py`（deployment-gated 注册：仅 `ENABLE_MANYFOLD_API` 时 include）。
- 解析 OpenAI 请求：
  - **`model` 字段 = agent_id**（Owner 已定，2026-05-25）。平台必须把目标 agent_id 放进 OpenAI 标准 `model` 字段，不另外通过 header 传。
  - `messages` 末条 user→input、`stream` 控制 SSE。
- 鉴权：`Authorization: Bearer {MANYFOLD_GATEWAY_TOKEN}` 校验（4.8）。
- 驱动：`_resolve_agent_owner(agent_id)`→creator user → 以 `working_source=MANYFOLD` + `ManyfoldContextBuilder` 起 `BackgroundRun`（复用 websocket.py 那套）。
- 翻译：BackgroundRun 事件 → OpenAI chunk —— `agent_thinking`/`token`→`choices[].delta.content`（thinking 是否回传由 capability 决定）、`reply_to_manyfold` 输出→主回复 content、terminal→`finish_reason:"stop"` + `data:[DONE]`。
- 非 stream：聚合成一个完整 `chat.completion` 响应。
- **错误响应**：映射成 OpenAI error 结构 + 合适 HTTP code；**且响应里的 `model` 字段必须回填请求里收到的 agent_id 原值**（不要返回空串、不要返回 `"narranexus"` 这种占位符——平台的错误日志可能按 model 字段聚合，回填正确值便于运维定位）。
  - 例：`{"error": {"message": "agent <agent_id> not found", "type": "invalid_request_error"}, "model": "<agent_id 原值>"}`

## 4.5 reply_to_manyfold tool + 回复捕获（agent 输出 → OpenAI SSE）✅ 专属 tool
agent 在 manyfold 渠道用**专属 `reply_to_manyfold` tool** 显式回复（语义同 IM 渠道的 reply tool）。endpoint 抓该 tool 的输出流式翻成 OpenAI chunk。
- 建轻量 `module/manyfold_module/`：
  - `_manyfold_mcp_tools.py`：定义 `reply_to_manyfold` MCP tool（入参就是要回给用户的文本/富内容）。MCP 端口 7833（或下一空闲）。
  - `manyfold_module.py`：最小 Module（暴露该 MCP tool + 注册）；`MessageSourceRegistry.register(...)`（参照 `telegram_module.py:112-121`）声明 `reply_to_manyfold` 是"用户可见回复"，使记忆持久化正确识别。
  - `manyfold_context_builder.py`（或在 ChannelTag/prompt 注入）：告诉 agent "你在 manyfold 渠道，用 `reply_to_manyfold` 回复"。
- endpoint 侧：订阅 BackgroundRun broadcaster，过滤出 `reply_to_manyfold` 的 tool call → 其文本转 `choices[].delta.content` 流式发送；terminal → `data:[DONE]`。`agent_thinking`/其他 `tool_call` 是否回传由 capability 决定。
- run 带 `WorkingSource.MANYFOLD` + manyfold ChannelTag（来源标记 + 记忆持久化一致）。
- 仍**无独立 trigger 进程、无 credential 表、不继承 ChannelTriggerBase**——只是多一个 MCP tool 的轻量 module。
- module 的注册随 `ENABLE_MANYFOLD_API` 门控（与端点同开关）；新增/改的 `.py` 同 commit 配 mirror md（守铁律 #10）。

## 4.6 跨 user 列 agent 端点
- `GET /manyfold/agents`（deployment-gated）：跨 user 查 `agents` 表全部，返回 id/name/description/created_by/created_at。
- 复用 `AgentRepository`，不走 `/api/auth/agents` 的 per-user 过滤（守铁律 #8 不污染多租户语义）。

## 4.7 `GET /healthz` + `HEAD /`
- `/healthz`：检查必须进程就绪（sqlite_proxy 可连 / DB 可查 / 关键 worker 在），返回 200 + JSON 状态。供平台 K8s probe + waitForReadiness。
- `HEAD /`：现有 SPA fallback 路由确认能返回 200（preflight 用）。

## 4.8 gateway-token 鉴权（统一一个 token 走三类入口）
**Owner 已定（2026-05-25）**：env 名 = `MANYFOLD_GATEWAY_TOKEN`，由 **Manyfold 平台**在 K8s Bootstrap 阶段 `randomBytes(32)` 生成，作为 K8s Secret 在容器启动时注入 env（与 openclaw `OPENCLAW_GATEWAY_TOKEN` 模式完全一致，参考 `bootstrap/openclaw.ts:28-29`）。容器只读 env，不自己生成。

**`backend/auth.py` 加第三种鉴权模式**（前两种是 local `X-User-Id` 和 cloud JWT）：deployment-gated（仅 `ENABLE_MANYFOLD_API=1` 时启用），对**三类路径**做校验：

| 路径 | 鉴权方式 | 谁会调 |
|------|----------|--------|
| `/v1/chat/completions` | `Authorization: Bearer {MANYFOLD_GATEWAY_TOKEN}` | Manyfold 平台（server → server）|
| `/manyfold/*` | 同上 | Manyfold 平台 |
| `/api/*` + `/ws/*`（native UI 走的）| **URL fragment `#token=` 兜底**，前端首次加载捕获后塞进所有 API 请求的 `Authorization: Bearer` 头 | 浏览器里的最终用户 |

**URL fragment 机制（解决 native UI 暴露问题）**：

参考 openclaw 的 control UI URL 模式（`k8s-runtime-sidecar.service.ts:215-218`）：

```
Manyfold 平台生成 URL：https://{ingressHost}/#token=<MANYFOLD_GATEWAY_TOKEN>
                                          ↑
                                  URL fragment 不会发到 server
                                  浏览器加载时由前端 JS 抓取
```

前端 bootstrap（`frontend/src/main.tsx` 或类似入口）：
1. 检测 `window.location.hash` 里有 `#token=<value>`
2. 抽出 value，存进 `sessionStorage` / 内存（不存 localStorage 避免跨 tab 泄漏）
3. **立即**从 URL 里抹掉 fragment（`window.history.replaceState`），避免后续刷新/分享 URL 漏 token
4. 之后所有 `/api/*` `/ws/*` 请求都带 `Authorization: Bearer <value>`
5. `auth.py` 在 `ENABLE_MANYFOLD_API=1` 时识别这个 header，把它当 `X-User-Id` 类的"已认证"凭证（用户身份由 backend 根据后续选 agent 时的 creator 推断——容器内"单用户共享凭证"假设见 Part 4.12）

**安全性**：
- ✅ token 在 URL fragment 里，**fragment 不会被发到 server**（浏览器历史/refer 也不带 fragment）
- ✅ 平台前置（Manyfold 自己的登录系统）已经认证过用户身份才会生成这个带 token 的 URL
- ✅ 即使有人拿到 ingressHost 想绕过平台直接打，没 token → 403
- ⚠️ token 进了内存，前端代码逻辑要避免把它泄漏到日志/控制台
- ⚠️ 容器重启后 token 通常会换（K8s Secret 重新生成），旧 URL 失效——平台需要在每次 chat session 开始时重新 mint URL（这是平台职责，不在我们这边）

**容器内单用户共享凭证假设**（Owner 已定，2026-05-25）：
一个 Manyfold runtime container 假设**只服务一个最终用户**（Manyfold per-user pod 模式）。容器内即使 NarraNexus 数据库存在多个 user row（旧机制保留），实际访问者只有一人，所有 Claude/provider 凭证共享。这把 native UI 鉴权简化为"门口一个 token"——进了门所有 agents/providers 都能看，不做 per-user 行级隔离。后续如果 Manyfold 走"一个 pod 多用户"模式再升级（属未来 scope，不在当前 spec）。

## 4.9 native UI 可反代
- 前端确认相对路径 API（不写死 `localhost:8000`）、WS 经反代（`wss://` + path）、必要时支持 base path（平台可能挂子路径）。
- 后端 CORS 放开平台域（`config.py`）。

## 4.10 部署开关
- backend：仅 `ENABLE_MANYFOLD_API` 存在时 include openai_compat router + `/manyfold/*` 路由（+ 可选 reply-tool module）。
- 门控的是**端点注册**，不是进程；本地版/云端版不设该 env → 端点 404、零行为变化。无需改 `dev-local.sh`/`run.sh` 起进程（不再有 trigger 进程）。

## 4.11 测试方案（TDD，每环节先写测试）
- 单测：OpenAI 请求解析、SSE 翻译（事件→chunk）、`reply_to_manyfold` 抓取、gateway-token 鉴权、跨 user 列表、healthz、deployment-gate 开关（env 开/关两态）。
- 集成：以 OpenAI 格式 curl 打通一轮（creator 身份 + manyfold channel 标记 + SSE `[DONE]`）。
- e2e（容器）：`docker run` + volume → 多 user 注册 / 建 agent / native UI 一轮 chat / 重启数据持久化；`ENABLE_MANYFOLD_API` 开关两态验证端点存在/404。

## 4.12 Claude 凭证管理（统一 Tauri / `bash run.sh` web / 容器三种模式）
**Owner 定调（2026-05-25）**：原来 `ProviderSettings.tsx` 里 OAuth login 按钮只在 Tauri 显示（走 Rust `trigger_claude_login` 命令调 `claude auth login` localhost callback）、web 模式只显示文字"自己去终端跑"——这套**在 Manyfold 容器里物理跑不通**（容器内 localhost callback 用户浏览器到不了），但 claude CLI 自身**支持** headless 友好的多种 auth 方式（[Authentication - Claude Code Docs](https://code.claude.com/docs/en/authentication)）。

**新设计**：UI 给两条路径，**三种模式（Tauri / web / 容器）统一走 backend HTTP，不再有 Tauri Rust 专属路径**。

### Option 1 · 粘 `CLAUDE_CODE_OAUTH_TOKEN`（推荐订阅用户）
- 用户在自己电脑跑 `claude setup-token`（任何能跑 OAuth 的机器都行，一次性）→ 终端打印一年期长效 token
- 用户回到 Settings → "Paste OAuth Token" 输入框 → Save
- 后端存 DB（新表 or 复用 provider 表，**实现时选最贴 NarraNexus 现有 provider config 路径**）
- 后端 spawn `claude` CLI 每个 turn 时把它注入为 `CLAUDE_CODE_OAUTH_TOKEN` env
- ✅ 容器内零 interactive auth，✅ 走用户自己的 Claude 订阅计费（Pro/Max/Teams），✅ 跨容器重启不丢

### Option 2 · 交互登录（callback 通就走 callback，不通自动回退 paste-code）
- 用户点 "Login with Claude" 按钮
- 后端启动 `claude auth login` 子进程
- claude CLI **自身**检测 callback 是否可达：
  - **通**（Tauri / 本地 `bash run.sh` 一般通）→ 浏览器自动跳转，用户登完即可，UI 显示进度即可
  - **不通**（Manyfold 容器 / 远程 SSH / WSL2）→ CLI 在 stdout 打印一个 URL + "Paste code here if prompted"，**前端把 URL 渲染出来 + 显示 code paste 输入框**
- 用户在自己浏览器打开 URL → 登录 → 拿到 code → 粘回 UI → 后端把 code 写进子进程 stdin → CLI 拿 code 自己去换 token，存进 `~/.claude/.credentials.json`（mode 0600）
- 容器场景：**`/home/app/.claude/` 必须是持久化 volume**（Part 4.1 已声明），否则重启即丢

### 后端新 endpoints（`backend/routes/claude_auth.py`）

| 路径 | 方法 | 作用 |
|------|------|------|
| `/api/auth/claude/status` | GET | 当前状态：`logged_in` / `oauth_token_set` / `nothing` |
| `/api/auth/claude/token` | POST | Option 1：保存粘贴的 token |
| `/api/auth/claude/token` | DELETE | 清除粘贴 token |
| `/api/auth/claude/login/start` | POST | Option 2：启动 `claude auth login` 子进程，返回 `session_id` |
| `/api/auth/claude/login/{sid}/events` | GET (SSE) | 流式吐出子进程 stdout（URL / 提示 / 完成）|
| `/api/auth/claude/login/{sid}/input` | POST | 用户粘 code 后，把内容写进子进程 stdin |
| `/api/auth/claude/login/{sid}/cancel` | POST | SIGTERM 子进程 + 清理 |
| `/api/auth/claude/logout` | POST | 调 `claude auth logout` |

子进程管理：`asyncio.subprocess` 启 `claude auth login`，stdout/stderr 用 line buffer 实时吐到 SSE，stdin 等用户 POST input。`session_id` 用进程内 dict 维护（重启容器丢，无所谓——重启等于登录中断，重新登）。

### 前端改动（`frontend/src/components/settings/ProviderSettings.tsx`）
- **去掉** `isTauri()` 门控（`:1144` 那段）——两个 Option 在所有模式下都显示
- **删掉** 那段 "Run `claude auth login` in your terminal" 文字（`:1178-1184`）——容器里那 terminal 够不到
- 新增 UI 区块：Option 1 输入框 + Option 2 按钮 + Option 2 触发后的渐进式 UI（URL 链接 + code 输入框）
- 文案明确告诉用户："Token paste = 给已经有 token 的人 / Interactive login = 没 token 现场登录"

### Tauri 删除项（守铁律 #2 不留兼容层）

| 文件 | 改动 |
|------|------|
| `tauri/src-tauri/src/commands/auth.rs` | 删 `trigger_claude_login` / `cancel_claude_login` / `get_claude_login_status` 三个函数（保留其他非 claude 的 auth 命令）|
| `tauri/src-tauri/src/lib.rs` | 从 `invoke_handler` 移除上述三个的注册 |
| `tauri/src-tauri/src/state.rs` | 删 `claude_login_pid: Arc<StdMutex<Option<u32>>>` 字段 |
| `frontend/src/lib/tauri.ts` | 删 `triggerClaudeLogin` / `cancelClaudeLogin` / `getClaudeLoginStatus` 三个导出 |

### 验收
- Tauri 启动 dmg → Settings 看到 Option 1 + Option 2，按钮可点 → Option 2 走完整 OAuth → 后续 chat 用上凭证
- `bash run.sh` 后浏览器访问 → 同上
- 容器（`ENABLE_MANYFOLD_API=1`）→ 同上，且 Option 2 会显示 URL + code 输入框（callback 不通的预期路径）
- 容器重启 → 持久化 volume 让上次登录的凭证还在，无需重登
- claude CLI 凭证 precedence（[Authentication Docs](https://code.claude.com/docs/en/authentication)）：`ANTHROPIC_API_KEY` > `ANTHROPIC_AUTH_TOKEN` > `apiKeyHelper` > `CLAUDE_CODE_OAUTH_TOKEN`（Option 1）> `~/.claude/.credentials.json`（Option 2）—— UI 状态展示要按此顺序判定"哪个在生效"

---

# Part 5 · 工作分类与代码改动清单

> Owner 要求（2026-05-25）：把所有改动**严格三分类**，每个改动归属到唯一一个 bucket，避免代码改散。**审查口径：每个 PR 严守对应 bucket 的文件边界**。

| Bucket | 性质 | 边界 |
|---|---|---|
| **#1 项目自身改进** | 通用收益，**和 Manyfold 没关系也应该做** | 改动遍及多层（前端/后端/Tauri/脚本），但每一处单独看都是 NarraNexus 自身的清洁/统一/增强 |
| **#2 Docker 打包** | 只为 Manyfold 部署而存在的打包工件 | **只有一个 Dockerfile**（含必要的 `.dockerignore`）。不动业务代码，不写独立 entrypoint |
| **#3 Manyfold API + bridge** | 对接 Manyfold 平台的桥接代码 | 只在 API 层、新建一个 manyfold module、加少量 env 读取。**全部由 `ENABLE_MANYFOLD_API` 门控**，不设该 env 时零行为变化 |

---

## 5.1 #1 · 项目自身改进（通用，不依赖 Manyfold）

**驱动这些改进的理由**：原有 Claude OAuth 登录只在 Tauri 显示按钮、web 模式让用户去终端跑、容器场景跑不通——这是个**长期就该统一的 UX 问题**，本次借势一次性把三种模式统一。`scripts/run.sh` 同理：dmg / 本地 / 容器三种场景应该共用一份脚本（守铁律 #7 的延伸）。

### 5.1.1 Claude 凭证管理 UX 统一

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `backend/routes/claude_auth.py` | **新增** | 8 个 endpoints（详 Part 4.12 表）：status / token CRUD / login session start+events+input+cancel / logout |
| `backend/main.py` | **修改** | include 上面这个 router（**不**门控——所有部署模式都暴露这套 endpoints）|
| `backend/auth.py` | **修改** | （仅 #3 用到，详 5.3）|
| `src/xyz_agent_context/...claude_oauth_store.py` 或复用 provider repo | **新增 or 复用** | Option 1 粘的 token 存哪——实现时选最贴现有 provider config 路径（最小变动）|
| `frontend/src/components/settings/ProviderSettings.tsx` | **重写** Claude 区块 | 去 `isTauri()` 门控（`:1144`），删 web fallback 文字段（`:1178-1184`），加 Option 1 输入框 + Option 2 按钮 + 渐进式 paste-code UI |
| `frontend/src/lib/tauri.ts` | **删除** | `triggerClaudeLogin` / `cancelClaudeLogin` / `getClaudeLoginStatus` 三个 wrapper（守铁律 #2 不留兼容）|
| `tauri/src-tauri/src/commands/auth.rs` | **删除函数** | `trigger_claude_login` / `cancel_claude_login` / `get_claude_login_status`（保留非 claude 的 auth 相关代码）|
| `tauri/src-tauri/src/lib.rs` | **修改** | 从 `invoke_handler` 移除上述三个的注册 |
| `tauri/src-tauri/src/state.rs` | **修改** | 删 `claude_login_pid` 字段 |
| `.mindflow/mirror/...` | **同 commit 更新** | 上述每个改了/删了的 `.py` `.rs` `.tsx` `.ts` 都要同步对应 mirror md（守铁律 #10）|

### 5.1.2 `scripts/run.sh` 容器友好改造

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `scripts/run.sh` | **修改** | 加 env 检测分支（`RUNTIME_MODE=container` / `IN_CONTAINER=1`）：路径默认走 `/data/*`、uvicorn 绑 `0.0.0.0`、日志全 stdout；**默认行为（无 env）= 现状**，保证 dmg / 本地 `bash run.sh` 零回归 |
| `scripts/dev-local.sh` | **不动** | 开发场景独立，不混入容器逻辑 |
| `src/xyz_agent_context/settings.py` | **修改** | 已有 `BASE_WORKING_PATH` env 化，补齐 `NEXUS_LOG_DIR`、narrative/trajectory 路径的 env fallback（如果还没的话），让 `run.sh` 设的 env 真能生效 |
| `src/xyz_agent_context/logging/_setup.py` | **修改**（若需）| 同上，确保 `NEXUS_LOG_DIR` env 优先级 |

### 5.1.3 #1 验收

- Tauri dmg 起来：Settings → Claude 区块看到 Option 1 + Option 2，点 Option 2 走完整 OAuth callback 流，登完再 chat 凭证生效
- `bash run.sh` 起来浏览器访问：同上
- `bash run.sh` 行为**完全不回归**（不设 `RUNTIME_MODE` env，路径/绑定行为同当前）
- 单测：claude_auth.py 所有 endpoint 路径打通；token CRUD 持久化正确；login session 生命周期（start → events stream → input → cancel）覆盖
- mirror md：所有动过的 `.py/.rs/.tsx/.ts` 都有对应 md 更新

---

## 5.2 #2 · Docker 打包（只有一个 Dockerfile）

**严格边界**：本 bucket **只有一个新文件 = `docker/manyfold/Dockerfile`**，加一个 `.dockerignore`（同目录）。**不**新增 `entrypoint.sh` / `docker-compose.yml` / 任何 Python / TypeScript / Rust 代码改动。

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `docker/manyfold/Dockerfile` | **新增**（唯一文件）| 多阶段：Stage 1 `node:22` build 前端 → Stage 2 `python:3.13-slim` 装 uv + node + claude CLI + uv sync + 拷源码 + 拷前端 dist；`EXPOSE 8000`；`CMD ["bash", "scripts/run.sh"]`；ENV 默认所有 `~` 路径指向 `/data/*` |
| `docker/manyfold/.dockerignore` | **新增** | 排 `.git` / `.venv` / `node_modules` / `reference/` / `drafts/` / `*.db` / `.env`（镜像绝不带 .env）|

### 5.2.1 #2 边界检查表（PR review 时严守）

| 不允许出现在 #2 PR 的改动 | 理由 |
|---|---|
| 修改 `scripts/run.sh` | 属 #1.2 |
| 修改 `backend/*.py` | 属 #1 或 #3 |
| 修改 `frontend/*` | 属 #1 或 #3 |
| 修改 `src/xyz_agent_context/*` | 属 #1 或 #3 |
| 修改 `tauri/*` | 属 #1.1 |
| 新建 entrypoint.sh | Owner 明确禁止——逻辑融进 `run.sh` |
| 新建 docker-compose.yml | 用户/平台/CI 自己组织 compose（如需），不在镜像 repo 里维护 |

### 5.2.2 #2 验收
- `docker build -f docker/manyfold/Dockerfile -t narranexus-manyfold .` 成功
- `docker run -p 8000:8000 -e ENABLE_MANYFOLD_API=1 -e MANYFOLD_GATEWAY_TOKEN=xxx -v ./data:/data -v ./claude:/home/app/.claude narranexus-manyfold` 起来
- 浏览器 `http://localhost:8000/#token=xxx` 能进 native UI
- `curl -H "Authorization: Bearer xxx" http://localhost:8000/healthz` 返 200
- 容器重启 → DB / 工作区 / claude credentials 都持久化（volume 生效）

---

## 5.3 #3 · Manyfold API + bridge

**严格边界**：本 bucket **只动 API 层、加一个 manyfold module、补少量 env 读取**。**全部由 `ENABLE_MANYFOLD_API` 门控**，不设该 env 时**端点 404 / module 不注册 / 行为零变化**——本地版/云端版/EC2 完全不受影响。

### 5.3.1 后端 API 层

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `backend/routes/openai_compat.py` | **新增** | `POST /v1/chat/completions`（详 Part 4.4）；model 字段 = agent_id；错误响应 model 回填 |
| `backend/routes/manyfold_agents.py` | **新增** | `GET /manyfold/agents`（跨 user 列 agent，详 Part 4.6）|
| `backend/routes/healthz.py` | **新增 or 直接 inline 到 main.py** | `GET /healthz`（详 Part 4.7）|
| `backend/main.py` | **修改** | 检测 `ENABLE_MANYFOLD_API` env，条件 include 上述三个 router；`HEAD /` preflight 走现有 SPA fallback（无需新增）|
| `backend/auth.py` | **修改** | 加第三种鉴权模式（`MANYFOLD_GATEWAY_TOKEN` Bearer 校验），覆盖 `/v1/*` `/manyfold/*` `/api/*` `/ws/*`；前端 URL fragment 捕获的 token 也走这条校验（详 Part 4.8）|

### 5.3.2 Manyfold Module（轻量 bridge）

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `src/xyz_agent_context/module/manyfold_module/__init__.py` | **新增** | 导出 `ManyfoldModule` |
| `src/xyz_agent_context/module/manyfold_module/_manyfold_mcp_tools.py` | **新增** | 定义 `reply_to_manyfold` MCP tool（参考 telegram_module 同名 tool 结构）|
| `src/xyz_agent_context/module/manyfold_module/manyfold_module.py` | **新增** | 最小 Module，`get_config()` 返回 `ModuleConfig(name="ManyfoldModule", priority=N, ...)`；MCP 端口 **7833**；`MessageSourceRegistry.register(...)` 注册 `reply_to_manyfold` 为"用户可见回复"（参考 `telegram_module.py:112-121`）|
| `src/xyz_agent_context/module/manyfold_module/manyfold_context_builder.py` | **新增** | ContextBuilder，告诉 agent "你在 manyfold 渠道，用 `reply_to_manyfold` 回复"（参考 ChannelTag 注入逻辑）|
| `src/xyz_agent_context/module/__init__.py` | **修改** | 在 `MODULE_MAP` 里**条件注册** ManyfoldModule（仅 `ENABLE_MANYFOLD_API=1` 时注册，避免污染本地/云端版的 module 列表）|
| `src/xyz_agent_context/schema/hook_schema.py` | **修改** | 加 `WorkingSource.MANYFOLD` 枚举值 |
| `src/xyz_agent_context/settings.py` | **修改** | 加 `ENABLE_MANYFOLD_API` / `MANYFOLD_GATEWAY_TOKEN` env 读取（只读，不入库）|

### 5.3.3 前端（Manyfold-specific bridge）

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `frontend/src/main.tsx` or `frontend/src/lib/auth.ts` | **修改** | 启动时检测 `window.location.hash` 里的 `#token=<value>`，抽出存内存，立即 `replaceState` 抹掉 URL；之后所有 fetch / WS 请求带 `Authorization: Bearer <value>` header；**条件激活**（fragment 不存在时不动现有逻辑）|
| `frontend/src/...` CORS / 反代相关 | **可能微调** | 确保所有 API 调用走相对路径（已有，确认即可）；WS 连接用相对 path（`wss://{host}/ws/...` 而非写死 `localhost:8000`，已有，确认即可）|

### 5.3.4 配置与文档

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `deploy/README.md` or `docs/manyfold-integration.md` | **新增** | 给 Ying 的接入文档：env 注入列表、image build 命令、native UI proxy 方式、OpenAI endpoint/鉴权契约、readiness 打 `/healthz` |
| 给 Ying 的对齐项 | **沟通** | workspace 路径规则 `{agent_id}_{user_id}`（注意顺序，以代码为准）、skill 目录 `skills/<name>/SKILL.md`、`K8S_IMAGE_NARRANEXUS` 配置位置 |

### 5.3.5 #3 边界检查表（PR review 时严守）

| 不允许出现在 #3 PR 的改动 | 理由 |
|---|---|
| 修改 `scripts/run.sh` | 属 #1.2 |
| 修改 `tauri/*` | 属 #1.1（且与 Manyfold 无关）|
| 改既有 module（awareness/chat/social/job/rag/skill 等）| Manyfold module 是独立新增，不应触动现有 |
| 新建数据库表 | 守 Part 2.2 Phase 2 "无新表" 约束 |
| 改 `auto_migrate` 逻辑 | 同上 |
| 修改 `backend/routes/auth.py` 之外的既有 routes | 既有 routes 不应感知 manyfold |

### 5.3.6 #3 验收
- `ENABLE_MANYFOLD_API=1` 启动 → `/v1/chat/completions` / `/manyfold/agents` / `/healthz` 都可达；带正确 `Authorization` 通过、错误 token 403
- 不设 `ENABLE_MANYFOLD_API` → 上述端点全 404；`MODULE_MAP` 不含 ManyfoldModule
- OpenAI 格式 curl 打一轮：model=agent_id → agent 以 creator 身份跑 → 用 `reply_to_manyfold` 回复 → SSE 流式 + `[DONE]` + 最后 chunk 的 `model` 字段 = agent_id
- 错误请求（非法 agent_id / messages 空）：返回 OpenAI error 结构，`model` 字段回填请求里的 agent_id 原值
- native UI 浏览器访问 `http://localhost:8000/#token=xxx` → 前端捕获 token → 所有 API 请求带 Authorization → 进得去；不带 fragment 直接访问 → 403
- mirror md：新增/改的 `.py` 都有对应 md（守铁律 #10）；既有 .py 的 mirror md 重读过确认 intent 未失效

---

## 5.4 三 Bucket 依赖关系 + PR 拆分建议

```
                #1.2 (run.sh 容器友好)
                       ↓
        ┌──────────────┴──────────────┐
        ↓                             ↓
  #2 (Dockerfile)              #3 (Manyfold API+bridge)
        ↓                             ↓
        └──────────────┬──────────────┘
                       ↓
              端到端验收（docker run + curl + 浏览器）
```

- **#1 与 #3 完全独立**——#1 改完合主线，本地版/云端版/EC2 立刻拿到收益（统一 Claude 凭证 UX）；#3 改完合主线，但默认不激活，等 #2 镜像 ship 后才在 Manyfold 部署里生效。
- **#2 依赖 #1.2 完成**——`scripts/run.sh` 容器友好后，Dockerfile 才能直接 `CMD ["bash", "scripts/run.sh"]`。
- **PR 拆分建议**：分 **3 个 PR**（甚至 4 个：`#1.1` Claude UX / `#1.2` run.sh / `#2` Dockerfile / `#3` Manyfold API），每个 PR 独立可 review、可回滚。

---

# Part 6 · 测试策略

> Owner 定调（2026-05-25）：Ying 休假期间无法做联合验证。**目标：等他回来一遍过，把"等他 debug → 我们 fix → 重 build → 重 deploy" 这个糟糕循环干掉**。手段：在我们这一侧把保真度推到 ~90%，剩下 ~10% 留给他真 prod 环境的最后一遍。**所有测试在本地 dev 机上跑，不依赖 EC2、不依赖真 EKS、不花云钱。**

## 6.1 测试金字塔（四层 + Ying-contingency 一层）

```
                       ┌─────────────────────────────┐
                       │ L5  Ying-runbook 一遍过      │  ← 真 Manyfold prod
                       │     (Ying 回来后)            │     不在本机跑
                       └─────────────────────────────┘
                       ┌─────────────────────────────┐
                       │ L4  本地 K8s E2E             │  ← k3d + 本地 Manyfold
                       │     真 K8s pod + 真 Ingress  │     + NarraNexusBootstrap
                       └─────────────────────────────┘
                     ┌─────────────────────────────────┐
                     │ L3  容器 smoke test              │  ← docker run + curl
                     │     单容器无 K8s                  │
                     └─────────────────────────────────┘
                  ┌─────────────────────────────────────┐
                  │ L2  进程内集成（FastAPI TestClient）  │  ← stub LLM
                  │     全部 manyfold endpoint 打通       │
                  └─────────────────────────────────────┘
               ┌───────────────────────────────────────────┐
               │ L1  单元测试（pytest + Vitest）             │
               └───────────────────────────────────────────┘
```

每层独立可跑、可 fail。CI 默认跑 L1+L2+L3；L4 是 milestone 门（Phase 2 → Phase 3 前必跑一次），L5 是 ship 门。

## 6.2 L1 单元测试

| Bucket | 测试范围 | 文件位置 | 工具 |
|---|---|---|---|
| #1.1 Claude UX backend | `claude_auth.py` 8 个 endpoint：mock `asyncio.subprocess`，断言 SSE 流格式 / token CRUD / session 生命周期 | `tests/unit/test_claude_auth.py` | pytest + httpx AsyncClient |
| #1.1 Claude UX frontend | ProviderSettings 在 `isTauri()=true/false` 两态渲染、Option 1/2 交互状态机、URL fragment token 捕获 | `frontend/src/components/settings/__tests__/ProviderSettings.test.tsx` | Vitest + React Testing Library |
| #1.2 run.sh | 语法 (`bash -n`) + env 驱动 dry-run（新增 `DRY_RUN=1` 分支只打印不执行）| `tests/unit/test_run_sh.bats` | bats（可选）|
| #3 OpenAI endpoint | 请求解析（model=agent_id 强校验）、SSE 翻译（事件→chunk，含 model 字段回填）、`[DONE]` sentinel 精确格式、错误响应 envelope | `tests/unit/test_openai_compat.py` | pytest |
| #3 manyfold_agents | 跨 user 查询不走 per-user 过滤、返回字段 schema | `tests/unit/test_manyfold_agents.py` | pytest |
| #3 auth.py | gateway-token 三态（无 / 错 / 对）+ URL fragment token 也通过同条逻辑 | `tests/unit/test_auth_manyfold_mode.py` | pytest |
| #3 manyfold_module | `reply_to_manyfold` tool 入参/出参、`MessageSourceRegistry` 注册、ContextBuilder 注入 | `tests/unit/module/test_manyfold_module.py` | pytest |
| #3 deployment-gate | `ENABLE_MANYFOLD_API` env 开关两态：开启注册全部端点 + module、关闭全 404 + MODULE_MAP 不含 | `tests/unit/test_deployment_gate.py` | pytest（FastAPI app fixture 用 env monkeypatch + 重 import）|

**关键 fixture**：**stub LLM provider**（属 #1 通用改进，先做）——一个 fake `BaseLLM` 子类，吃 OpenAI 请求格式、返回固定 token 流。让所有 chat-related 测试不打真模型，**这是上面所有 L2/L3/L4 测试的基础**。位置：`tests/fixtures/stub_llm_provider.py`。

## 6.3 L2 进程内集成测试（FastAPI TestClient）

**目标**：在一个 Python 进程里把"OpenAI 请求 → BackgroundRun → manyfold module reply → SSE 翻译"完整打通，**不起容器、不起 K8s**。

```python
# tests/integration/test_manyfold_e2e.py
@pytest.fixture
async def manyfold_app(stub_llm_provider, tmp_path):
    os.environ["ENABLE_MANYFOLD_API"] = "1"
    os.environ["MANYFOLD_GATEWAY_TOKEN"] = "test-token"
    # 用 sqlite tmp_path 起一份完整 NarraNexus app
    from backend.main import app
    yield app

async def test_full_chat_roundtrip(manyfold_app, sample_agent_id):
    async with httpx.AsyncClient(app=manyfold_app, base_url="http://t") as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"model": sample_agent_id, "messages": [{"role":"user","content":"hi"}], "stream": True},
        )
        chunks = parse_sse(resp.text)
        assert all(c["model"] == sample_agent_id for c in chunks if "model" in c)
        assert chunks[-1] == "[DONE]"
        # 断言 agent 真的用了 reply_to_manyfold tool（不是直接 token 流）
        ...
```

**覆盖的核心场景**（每个一个 test）：
1. happy path：stream + 非 stream 两版
2. 鉴权失败：无 token / 错 token → 401 / 403
3. 非法 agent_id → 错误响应，model 字段回填请求原值
4. `ENABLE_MANYFOLD_API` 不设 → endpoint 404
5. native UI 鉴权：URL fragment token 模拟（前端逻辑用 jsdom 测，或 backend 测"带 Authorization header 走 /api/* 能通"）
6. 跨 user 列 agent：建多个 user 多个 agent，断言 `/manyfold/agents` 全部能看到

## 6.4 L3 容器 smoke test

**目标**：Dockerfile 真 build、真 run，确认所有进程跑起来、端口对、env 注入生效。**不起 K8s**。

`tests/container/smoke.sh`：

```bash
#!/bin/bash
set -euo pipefail

IMAGE=narranexus-manyfold:test

# 1. Build
docker build -f docker/manyfold/Dockerfile -t "$IMAGE" .

# 2. Run with manyfold env
docker run -d --name nx-smoke \
  -p 8000:8000 \
  -e ENABLE_MANYFOLD_API=1 \
  -e MANYFOLD_GATEWAY_TOKEN=smoke-test \
  -e CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN \
  -v "$PWD/.tmp/data:/data" \
  -v "$PWD/.tmp/claude:/home/app/.claude" \
  "$IMAGE"

# 3. Wait for ready
for i in {1..60}; do
  curl -sf http://localhost:8000/healthz && break
  sleep 2
done

# 4. Smoke assertions
curl -sf -X HEAD http://localhost:8000/                              || exit 1  # preflight
curl -sf http://localhost:8000/healthz                               || exit 1
curl -sf http://localhost:8000/manyfold/diagnostics \
     -H "Authorization: Bearer smoke-test" | jq -e '.checks | all'  || exit 1
curl -sf http://localhost:8000/manyfold/agents \
     -H "Authorization: Bearer smoke-test"                          || exit 1
# (chat completions 留给 L2 测，这里只确认路由存在)

# 5. Restart persistence
docker restart nx-smoke
sleep 5
curl -sf http://localhost:8000/healthz                               || exit 1

# 6. Deployment gate (relaunch without env)
docker stop nx-smoke && docker rm nx-smoke
docker run -d --name nx-smoke2 -p 8001:8000 "$IMAGE"
sleep 10
curl -sf http://localhost:8001/healthz                               || exit 1
[ "$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/v1/chat/completions)" == "404" ] || exit 1
docker stop nx-smoke2 && docker rm nx-smoke2

echo "✅ container smoke passed"
```

放 CI 里跑（GitHub Actions docker-in-docker，或本地 `make test-container`）。

## 6.5 L4 本地 K8s E2E（**最关键，替代真 EKS 的中间档**）

**目标**：在 dev 机上模拟"Manyfold 平台 + K8s 集群"完整闭环，**只用免费工具，零云开销**，抓出 L1-L3 抓不到的所有"K8s 真实部署"问题。

### 6.5.0 本地 setup 总览（先看这一节）

#### 两种本地测试模式

| Mode | 起 K8s | 抓的问题 | setup 时间 | 何时用 |
|------|--------|----------|-----------|--------|
| **A · 控制面 + 直连容器** | ❌ 不起 | OpenAI 协议契约、SSE 格式、URL fragment 鉴权、Manyfold 控制面 ↔ NarraNexus 全链路 | ~30 分钟 | **先做这个**——风险/收益比最好，证明协议层通了 |
| **B · 完整 K8s 模拟** | ✅ k3d | A 的全部 + K8s 资源、readiness probe、Bootstrap env 注入、PVC 持久化 | ~1-2 小时 | Phase 2 收尾 milestone 门 |

Mode A 把 NarraNexus 容器跑在本地 docker，让 Manyfold 把它当 `external` runtime（一个固定 HTTP URL 的 agent runtime，Manyfold 直接打 `http://localhost:8000/v1/chat/completions`）。Mode B 是 6.5.1 描述的完整 k3d + NarraNexusBootstrap.ts 流程。

#### 数据流图（两侧 DB 完全隔离）

```
┌───────────────────────────────────────────────────────────────┐
│ 你的 dev 机                                                     │
│                                                               │
│  ┌─ Manyfold 控制面（just dev） ─┐                              │
│  │  api :2222                  │  ←──→  ┌─ nca-pg ──────────┐  │
│  │  admin :3001                │        │ Postgres 16       │  │
│  │  web :3002（如启动）          │        │ db = nca          │  │
│  └──────────┬──────────────────┘        │ vol: nca-pg-data  │  │
│             │ HTTP                       └───────────────────┘  │
│             ↓                                                   │
│  ┌─ NarraNexus 容器 ─────────────┐                              │
│  │  uvicorn :8000               │       ┌─ SQLite ─────┐        │
│  │  + sqlite_proxy :8100        │ ────→ │ /data/       │        │
│  │  + MCP :7801-7833            │       │ nexus.db     │        │
│  │  + Poller / Job / Bus        │       │ vol: .tmp/   │        │
│  │                              │       │      data    │        │
│  └──────────────────────────────┘       └──────────────┘        │
└───────────────────────────────────────────────────────────────┘
```

#### 数据库说明（两组 DB 完全隔离）

| DB | 谁的 | 起法 | 内容 |
|----|------|------|------|
| **Postgres 16**（容器 `nca-pg`，端口 :5432）| Manyfold | `just bootstrap` 自动执行 `docker compose up -d postgres`——**你不用手动起** | Manyfold 自己的 user / agent / runtime / credentials 元数据 |
| **SQLite**（`/data/nexus.db`，文件，无端口）| NarraNexus | 容器启动时 `auto_migrate()` 幂等建表，文件落在 docker volume `$PWD/.tmp/data:/data` | NarraNexus agent / narrative / event / module instance 数据 |

> **如果将来真接 MySQL**（spec Part 2.1 保留方案）：设 `DATABASE_URL=mysql://...`，跳过 sqlite_proxy 进程，连外部 MySQL 实例。本地测试**用不到**。

#### 必填的 3 个 env（其他全部可空）

只编辑 `../reference/netmind-cloud-agents/apps/api/.env`：

```bash
# 1. 加密 key（用 node 生成 32 字节 base64）
API_CRYPTO_KEY=$(node -e "console.log(require('crypto').randomBytes(32).toString('base64'))")

# 2. 首次 setup 用的临时 token（任意字符串）
AUTH_SETUP_TOKEN=$(openssl rand -hex 16)

# 3. admin 邮箱（你自己的）
ADMIN_EMAILS=bin.liangmathematicsstudent@gmail.com
```

#### 可以安全留空的（本地测试不需要）

| Env 组 | 作用 | 本地留空的后果 |
|--------|------|----------------|
| `SUB2API_*` | Manyfold 的"托管 model provider"（自动 lazy-provision sub2api 用户）| 我们用自己的 API key，不走 Sub2API |
| `GITHUB_TOKEN` | Skills 发现走 GitHub 公开 API | 匿名 rate limit，本地够用 |
| Clerk 相关（如有）| 生产用 Clerk 鉴权 | Manyfold 检测到没配 Clerk → 用 `AUTH_SETUP_TOKEN` 首次启动 fallback 路径，admin 邮箱直接成 owner |
| `K8S_*` cluster 配置 | 真 K8s 部署 | Mode A 不需要；Mode B 用 k3d 时通过 admin UI 注册 |

#### Mode A 完整启动命令（5 步）

```bash
# 1. 装 just（缺）
sudo apt install just   # 或 cargo install just

# 2. 起 Manyfold（一次性 bootstrap，之后只需 just dev）
cd ../reference/netmind-cloud-agents
cp apps/api/.env.example apps/api/.env
# 编辑 .env：填上面 3 个必填项
just bootstrap        # pnpm install + docker postgres + drizzle migrate，~3-5 分钟
just dev              # api :2222 + admin :3001 + web :3002

# 3. 浏览器 http://localhost:3001/setup，用 AUTH_SETUP_TOKEN 完成首次配置

# 4. 在另一个终端，build + run 我们的容器
cd NarraNexus
docker build -f docker/manyfold/Dockerfile -t narranexus-manyfold:dev .
docker run -d -p 8000:8000 \
  -e ENABLE_MANYFOLD_API=1 \
  -e MANYFOLD_GATEWAY_TOKEN=local-test-token \
  -e CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN \
  -v $PWD/.tmp/data:/data \
  -v $PWD/.tmp/claude:/home/app/.claude \
  --name narranexus-manyfold \
  narranexus-manyfold:dev

# 5. 在 Manyfold admin 建 external runtime：
#    URL = http://host.docker.internal:8000
#    Token = local-test-token
#    建一个 agent → 发消息 → 看回复 ✅
```

#### Mode A 验收（**Phase 1/2 完成后第一个该过的门**）

- Manyfold web UI 能创建 agent 并发送消息
- 消息走通：Manyfold api → POST `http://host.docker.internal:8000/v1/chat/completions` with `Authorization: Bearer local-test-token` → SSE 流回 → web UI 显示 agent 回复
- 错误路径：错 token → 403；不带 token → 401
- 容器重启后 `$PWD/.tmp/data` 数据保留，重启后立刻可继续聊
- `curl http://localhost:8000/manyfold/diagnostics -H "Authorization: Bearer local-test-token" | jq` 全部 ✅

**Mode A 通过即证明 OpenAI 协议契约 100% 对**——后面 Mode B 的失败只可能在 K8s 层，不可能在协议层。

### 6.5.1 Mode B 技术栈（在 Mode A 通过后才做）

| 组件 | 选型 | 理由 |
|------|------|------|
| 本地 K8s | **k3d**（k3s in Docker）| 单二进制，启动 30s，自带 LoadBalancer + Traefik ingress + 本地 registry |
| 本地 image registry | `k3d cluster create --registry-create` 自动起 | 不用 push 到 Docker Hub |
| Manyfold 控制面 | `just bootstrap` + `just dev` 起 netmind-cloud-agents（本地 :2222 + :3001）| 真 Manyfold |
| `NarraNexusBootstrap` 适配器 | **我们自己写**，提交到 netmind-cloud-agents 的 fork | 见 6.5.3 |
| 域名 / Ingress 路由 | `/etc/hosts` 加 `narranexus-test.localhost` | 不需要真 DNS |
| TLS | 关掉（本地 plain HTTP）或 mkcert 自签 | 不要 Let's Encrypt |

**准备工作**（一次性 setup，估计本机 30-60 分钟）：

```bash
# 1. 装 k3d
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# 2. 起本地 cluster + registry
k3d cluster create manyfold-test \
  --registry-create manyfold-registry:5000 \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer" \
  --agents 1

# 3. 装 nginx-ingress（替代 Traefik 更贴近真 prod）
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# 4. clone + 起 Manyfold 控制面
cd ../reference/netmind-cloud-agents
just bootstrap
just dev   # api :2222 + admin :3001

# 5. 把我们的 image build + push 到本地 registry
docker build -f docker/manyfold/Dockerfile -t localhost:5000/narranexus-manyfold:dev .
docker push localhost:5000/narranexus-manyfold:dev

# 6. 在 Manyfold admin 配 K8S_IMAGE_NARRANEXUS=manyfold-registry:5000/narranexus-manyfold:dev
#    并把 k3d 当 cluster 注册进 Manyfold
```

### 6.5.2 E2E 测试场景

`tests/k8s_e2e/test_narranexus_runtime.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 在 Manyfold admin web UI 建一个 narranexus agent | Manyfold 调 `NarraNexusBootstrap.plan()` → 生成 K8s manifests → apply → pod ready |
| 2 | 等待 readiness probe | `GET /healthz` 返 200 后 Manyfold 把 agent 标 ready |
| 3 | 通过 Manyfold web UI 发一条消息 | Manyfold 走 ApiChatAdapter → preflight HEAD `/` → POST `/v1/chat/completions` → SSE 流 → 显示回复 |
| 4 | 走 control-UI URL（`https://.../#token=xxx`）打开 native UI | 前端捕获 token → 后续 API 请求带 Authorization → UI 全功能可用 |
| 5 | pod 重启（`kubectl rollout restart`）| credentials volume 保住 → 重启后无需重登 |
| 6 | env 错误（漏 `MANYFOLD_GATEWAY_TOKEN`）| pod 起来但所有 manyfold endpoint 403 → readiness 也失败（验证 fail-fast 行为）|

### 6.5.3 `NarraNexusBootstrap.ts`（自己先写）

这份代码**本身就是要给 Ying 的产出**。在 netmind-cloud-agents fork 里加：

```
apps/api/src/modules/agents/bootstrap/narranexus.ts         # 新增
apps/api/src/modules/agents/bootstrap/narranexus.module.ts  # NestJS module
apps/web/src/lib/agentCreate/frameworkOptions.ts            # 修改：去 disabled
```

具体内容（照 `openclaw.ts` 改）：

```ts
const PORT = 8000
const MOUNT = `${K8S_HOME_BASE}/.narranexus`

@Injectable()
export class NarraNexusBootstrap implements K8sFrameworkBootstrap {
  readonly framework = 'narranexus' as const
  plan(ctx: K8sBootstrapContext, credentials: unknown): K8sBootstrapPlan {
    const creds = credentials as ResolvedNarraNexusCredentials
    const gatewayToken = creds.gatewayToken ?? randomBytes(32).toString('hex')
    return {
      framework: 'narranexus',
      port: PORT,
      pvcMountPath: MOUNT,
      envSecretData: {
        ENABLE_MANYFOLD_API: '1',
        MANYFOLD_GATEWAY_TOKEN: gatewayToken,
        // claude credentials（Option 1 token 或挂 PVC 让 Option 2 写入）
        CLAUDE_CODE_OAUTH_TOKEN: creds.claudeOauthToken ?? '',
        // 其他 LLM provider env（按 NarraNexus 现有约定）
        ANTHROPIC_API_KEY: creds.anthropicApiKey ?? '',
        BASE_WORKING_PATH: `${MOUNT}/workspaces`,
        DATABASE_URL: `sqlite:///${MOUNT}/nexus.db`,
        RUNTIME_MODE: 'container',  // 让 scripts/run.sh 知道是容器
      },
      readinessProbe: {
        httpGet: { path: '/healthz', port: PORT },
        initialDelaySeconds: 30,
        periodSeconds: 10,
        failureThreshold: 60,
      },
      httpReadinessPath: '/healthz',
      generatedCredentials: { gatewayToken },
      resources: {
        requests: { cpu: '500m', memory: '2Gi' },   // NarraNexus 比 openclaw 重
        limits: { cpu: '2000m', memory: '4Gi' },
      },
    }
  }
}
```

**这份 TS 文件是 Phase 3 给 Ying 的核心交付物**。我们在 L4 测试时**先用自己这份**跑 E2E，等他回来 review + 微调即可。

### 6.5.4 L4 验收

- `make k8s-e2e` 一键跑通上述 6 个场景全 ✅
- 测试报告里包含 `kubectl describe pod` / `kubectl logs` 的关键截屏，证明真在 K8s 里跑
- `NarraNexusBootstrap.ts` PR 已 ready（在我们这边的 fork 里，等 Ying 来 merge）

## 6.6 L5 Ying 回来后的 ship 验证（**最短路径 runbook**）

ship 给 Ying 的包含三样：

1. **镜像**：`docker pull narranexus/manyfold:vX.Y.Z`（或本地 build 命令）
2. **PR**：在 netmind-cloud-agents 仓的我们 fork，含 `NarraNexusBootstrap.ts` + frameworkOptions 改动，Ying review + merge
3. **Runbook**：`docs/manyfold-integration.md`

Runbook 内容（**目标：Ying 操作 < 30 分钟**）：

```markdown
# Ying 的最短验证路径

## 前置
- 我们提供的镜像已 push 到 Manyfold 私有 registry
- 我们的 NarraNexusBootstrap PR 已 merge 到 main
- `K8S_IMAGE_NARRANEXUS` 已配在 Manyfold prod env 中

## 步骤（5 步）
1. 在 web UI 建一个 narranexus agent（dropdown 里 narranexus 已可选）
2. 等 ready（~1 分钟，readiness probe 通过）
3. 在 web UI 发条消息，看到回复
4. 点 "Open control UI"，看到 NarraNexus native UI
5. 跑 smoke：`curl https://<agent-host>/manyfold/diagnostics`，每项都 ✅

## 失败时第一动作
SSH 进 pod（或 kubectl exec）→ `curl localhost:8000/manyfold/diagnostics`
对照下表定位：
| diagnostic 项 | 失败 → 看 |
| ... | ... |
```

## 6.7 `/manyfold/diagnostics` 自检端点

**新增**（属 #3 bucket）：`backend/routes/manyfold_diagnostics.py`，仅 `ENABLE_MANYFOLD_API=1` 时注册。

```python
@router.get("/manyfold/diagnostics")
async def diagnostics(auth=Depends(require_manyfold_token)):
    return {
        "image_version": settings.IMAGE_VERSION,
        "checks": {
            "sqlite_proxy_alive": await check_sqlite_proxy(),
            "mcp_servers_listening": await check_mcp_ports([7801,7802,7803,7804,7805,7806,7833]),
            "claude_cli_installed": shutil.which("claude") is not None,
            "claude_credentials_configured": await check_claude_credentials(),
            "frontend_dist_present": (settings.frontend_dist / "index.html").exists(),
            "gateway_token_set": bool(os.environ.get("MANYFOLD_GATEWAY_TOKEN")),
            "writable_data_dir": os.access("/data", os.W_OK),
            "writable_claude_dir": os.access("/home/app/.claude", os.W_OK),
        },
        "warnings": [...],
    }
```

**用途**：L3 / L4 / L5 各层验收的"第一道光"——5 秒返回容器自检结果。

## 6.8 关键 mock：`tests/e2e/mock_manyfold_adapter.py`

**严格按** Manyfold 真 adapter 源码（`openclaw.adapter.ts:90-328`、`hermes.adapter.ts:92` 等）逐行模仿，每个 assert 注释里 cite 源码行号：

```python
# tests/e2e/mock_manyfold_adapter.py
class MockManyfoldAdapter:
    """逐行模仿 Manyfold 平台真 adapter 调用我们的方式。
    
    源代码参考：
    - openclaw.adapter.ts:90-328 (主流程)
    - openclaw.adapter.ts:175 (preflight HEAD 5s)
    - agent-ingress.ts:23 (agentBaseUrl scheme)
    """
    
    async def preflight(self, ingress_host: str, token: str):
        """openclaw.adapter.ts:175: HEAD / with 5s timeout, expect any 2xx"""
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.head(f"https://{ingress_host}/",
                                headers={"Authorization": f"Bearer {token}"})
            assert 200 <= resp.status_code < 300
    
    async def chat_completion(self, ingress_host, token, agent_id, messages):
        """openclaw.adapter.ts:90-328"""
        async with httpx.AsyncClient(timeout=300) as c:
            async with c.stream("POST", f"https://{ingress_host}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"model": agent_id, "messages": messages, "stream": True},
            ) as resp:
                assert resp.status_code == 200
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        body = line[6:]
                        if body == "[DONE]":
                            events.append(("done", None))
                            break
                        chunk = json.loads(body)
                        # openclaw.adapter.ts:200+ 解析逻辑
                        assert chunk.get("model") == agent_id  # 我们的契约
                        events.append((chunk["choices"][0].get("delta"), chunk))
                return events
```

**这份 mock 在 L2 / L4 测试都会用到**——L2 用 TestClient 跑，L4 用真 HTTP 跑同一份 adapter，证明我们的容器对协议的兼容性 100%。

## 6.9 全部测试一表速查

| 层 | 命令 | CI 自动跑 | Phase 节点 |
|---|---|---|---|
| L1 单元 | `make test-unit` (pytest + vitest + bash -n) | ✅ 每 PR | 持续 |
| L2 进程内集成 | `make test-integration` | ✅ 每 PR | 持续 |
| L3 容器 smoke | `make test-container` | ✅ 每 PR（docker-in-docker）| 持续 |
| L4 本地 K8s E2E | `make test-k8s-e2e`（一次性 setup k3d，之后增量）| ⚠️ milestone 触发 | Phase 2 → Phase 3 门 |
| L5 Ying 真 prod | runbook 手工跑 | ❌ 不能自动 | Ship 前 |

## 6.10 时间投入估算（按结构性维度）

- **stub LLM provider**：1 个新 fixture 文件，~50 行
- **claude_auth.py 单测**：1 个测试文件，8 个 endpoint × 3 个 case ≈ 24 test
- **openai_compat / manyfold_agents / auth.py 单测**：3 个文件，~30 test
- **mock_manyfold_adapter.py**：1 个文件，~150 行（含 cite 注释）
- **smoke.sh**：1 个 bash 脚本，~50 行
- **k3d setup + Manyfold 本地启动**：一次性 setup，无新代码（操作步骤进文档）
- **NarraNexusBootstrap.ts**：1 个 TS 文件，~80 行（照抄 openclaw.ts）
- **diagnostics endpoint**：1 个 router，~50 行
- **Ying-runbook**：1 个 md 文档，~1 页

> **没估天数**——见铁律 #17。结构上是 ~10 个新文件、所有都简短、互相独立可并行，因此可以拆 ~10 个 PR 同时进行。

---

# 附录 A · 调研事实基线（NarraNexus 侧）

### A.1 运行拓扑（`run.sh` / `dev-local.sh`）
必须进程（最小集）：SQLite Proxy `:8100`（`xyz_agent_context.utils.sqlite_proxy_server`）、Backend `:8000`（`uvicorn backend.main:app --host 0.0.0.0`）、MCP `:7801-7820`（`module.module_runner mcp`）、Poller、Job Trigger、Bus Trigger。可选：Lark/Slack/Telegram trigger（7830-7832）。
- sqlite_proxy 独占 SQLite 文件句柄消除多进程锁竞争；`DATABASE_URL=mysql://` 时 `db_factory.py:206-227` 直连、跳过 proxy。
- 前端：`make build-frontend`→`frontend/dist`；`backend/main.py:330-343` 已挂 StaticFiles + SPA fallback，单端口 8000 同 serve API+前端。
- 依赖：Python ≥3.13（uv）、Node/npm、`@anthropic-ai/claude-code` CLI（硬依赖，每 chat turn spawn `claude`）。
- 硬编码 `~` 路径：`BASE_WORKING_PATH=~/.nexusagent/workspaces`（`settings.py:142`）、`NEXUS_LOG_DIR=~/.narranexus/logs`（`logging/_setup.py:36`）、narrative/trajectory（`settings.py:148-149`）、SQLite DB `~/.narranexus/nexus.db`。

#### A.1.1 五种部署模式下 8000 端口的职责对比

| 部署模式 | host 入口 | 中间层 | `:8000` 跑什么 | 前端在哪 | 备注 |
|---------|----------|--------|---------------|---------|------|
| **EC2 生产**（当前线上 `agent.narra.nexus`）| Caddy host `:80/:443`（auto-HTTPS + Host 路由）| frontend container **nginx :80**（独立容器）proxy `/api/*` `/ws/*` 到 backend | uvicorn FastAPI（**只 API**，不挂 StaticFiles）| 独立 nginx 容器 `/usr/share/nginx/html` | 长生命周期生产实例，分容器有运维好处。参考 `stacks/narranexus-app/compose.yml`。**不是 Manyfold 容器架构** |
| **开发模式**（`make dev-frontend` + `make dev-backend`）| 无 | Vite dev server **:5173**（HMR）proxy `/api/*` `/ws/*` 到 8000 | uvicorn FastAPI（**只 API**，没 `frontend/dist` 时不挂 StaticFiles）| Vite :5173 内存里热重载 | `:5173` 仅开发时存在，**容器内不存在** |
| **`bash run.sh` 生产**（`make build-frontend` 后跑）| 无 | 无 | uvicorn FastAPI（**同时 serve 前端 dist + API**，StaticFiles + SPA fallback）| FastAPI 进程内静态文件 | 单进程单端口 |
| **Tauri (dmg)** | 无 | 无 | sidecar 起的 uvicorn（**同时 serve 前端 + API**）；WebView 直连 | FastAPI 进程内静态文件（前端打包进 dmg）| 单进程单端口，等价于 `bash run.sh` 生产 |
| **Manyfold 容器（本 spec 目标）** | Manyfold Ingress（公网 HTTPS）| → 容器 `:8000` | uvicorn FastAPI（**同时 serve 前端 + API + `/v1/chat/completions` + `/manyfold/*` + `/healthz`**）| FastAPI 进程内静态文件 | 单容器单端口（Manyfold 硬约束）。**与 EC2 不同**（不分容器），**与 `bash run.sh` 生产 / Tauri 一致**（守铁律 #7）|

**关键认知**：本 spec 设计的 Manyfold 容器架构是 "`bash run.sh` 生产 / Tauri" 这一脉，**不是** EC2 当前生产架构的复用。原因：Manyfold 单端口契约决定了不能分容器；而 EC2 分容器是出于运维便利，不是必须。两套架构并存，分别服务不同场景。

### A.2 现有 API（`backend/routes/`）
- 列 agent：`GET /api/auth/agents`（per-user，`auth.py:286-463`）。
- 原生对话：`WS /ws/agent/run`（BackgroundRun，事件 run_started/agent_response/agent_thinking/tool_call/run_ended 等）。
- 配 key：`/api/providers/*` 全 CRUD。
- 定时任务：`GET /api/jobs?agent_id=`。
- 鉴权：local `X-User-Id` header / cloud JWT Bearer。

### A.3 适配细节
- workspace：`{BASE_WORKING_PATH}/{agent_id}_{user_id}/`（`attachment_storage.py:88`）—— **agent 在前**，与会议口述相反，以代码为准。
- skill：`{workspace}/skills/<name>/`，含 `SKILL.md`（`skill_module.py:212`）。
- key env 覆盖：`.env` > shell env > `llm_config.json`（`settings.py:57-81`）。**镜像内不带 `.env`** 才能让平台注入 env 生效；云模式有 `SYSTEM_DEFAULT_LLM_*`（`system_provider_service.py:64-85`）。

---

# 附录 B · 决策状态（全部敲定）

**2026-05-22 Owner 定调（架构层）：**
- ✅ 一个容器 = 本地版多 user/多 agent 实例（不做 per-agent pod）；**容器内单用户共享凭证假设**（per-user pod 模式，2026-05-25 补充）
- ✅ native UI 映射 + OpenAI chat endpoint 双轨
- ✅ 默认 SQLite + volume，保留 MySQL
- ✅ 跨 user 列 agent，走 deployment-gated 端点
- ✅ **chat = API 里多一个 endpoint，进程内驱动 BackgroundRun，不要独立 trigger / 不继承 ChannelTriggerBase**
- ✅ deployment-gate 门控端点注册（`ENABLE_MANYFOLD_API`），不是进程
- ✅ v1 选型见 §3.2
- ✅ agent 用**专属 `reply_to_manyfold` tool 显式回复**（语义上 manyfold 是独立渠道）；需加轻量 module 注册该 MCP tool + `MessageSourceRegistry`；endpoint 抓该 tool 的输出翻成 SSE

**2026-05-25 Owner 定调（细节层，本次讨论敲定）：**
1. ✅ **命名**：本文用 `manyfold`（对齐平台代码包 `@manyfold/shared`），最终确认。
2. ✅ **transport 细节**：
   - **agent_id 放 OpenAI `model` 字段**（不放 header），错误响应也回填 `model` 字段为请求里的 agent_id 原值（详 Part 4.4）
   - **gateway-token 走 env 注入**：env 名 `MANYFOLD_GATEWAY_TOKEN`，由 Manyfold 平台在 K8s Bootstrap 阶段 `randomBytes(32)` 生成并注入（对齐 openclaw `bootstrap/openclaw.ts:28-29` 模式，详 Part 4.8）
3. ✅ **native UI 鉴权**（暴露后）：**平台前置 + URL fragment `#token=` 兜底**，参考 openclaw `k8s-runtime-sidecar.service.ts:215-218`。与 #2 同一个 `MANYFOLD_GATEWAY_TOKEN` 复用，前端首次加载捕获 fragment → 内存存储 → 抹掉 URL → 所有 `/api/*` `/ws/*` 请求带 `Authorization` header；`auth.py` 加新模式校验（详 Part 4.8）
4. ✅ **Claude CLI auth**：**支持 Option 1（粘 `CLAUDE_CODE_OAUTH_TOKEN`）+ Option 2（交互登录，callback 通就走 callback、不通自动回退 paste-code）**，统一 Tauri / `bash run.sh` web / 容器三种模式（全走 backend HTTP），Tauri Rust claude 命令删除。`SYSTEM_DEFAULT_LLM_*` 云模式机制保留（平台可选注入"统一池子 key"）。详 Part 4.12。
5. ✅ **路径前缀规范**：manyfold 自定义端点统一 `/manyfold/*` 前缀；OpenAI 标准端点 `/v1/chat/completions` 因协议约束留根（例外）。
6. ✅ **8000 端口语义注脚**：Part 4.1 + 附录 A.1.1 加入 5 种部署模式（EC2 生产 / 开发 / `bash run.sh` 生产 / Tauri / Manyfold 容器）下 8000 端口职责对比表，避免与现有 EC2 架构混淆。
7. ✅ **容器化只有一个 Dockerfile**：不写独立 `entrypoint.sh` / `docker-compose.yml`；容器启动逻辑融入 `scripts/run.sh`（守铁律 #7），Dockerfile `CMD` 直接调它。详 Part 4.1 + 4.2。

**当前所有设计决策全部敲定，可以进入 TDD 实现阶段。**

---

# 附录 C · 风险 / TODO
- 容器内 `claude` CLI 无 TTY 鉴权需实测。
- workspace 路径顺序 `{agent_id}_{user_id}`，澄清给 Ying。
- 平台 per-agent pod 与我们多 agent 实例的张力：平台需把 NarraNexus 当实例级 runtime 注册（而非 per-agent 拉 pod），须和 Ying 确认平台侧编排能支持。
- MCP 端口（7801-7820）是否单进程监听待确认（不影响 Dockerfile）。
