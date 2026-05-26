# 2026-05-26 午休时段独立工作汇报

## 总结一句话

**完整跑通了**：Manyfold UI ↔ NarraNexus 容器双向通信，agent 自动配置，chat 真返回，历史持久化。回来打开 http://localhost:3002 直接体验。

---

## 你提的 6 个目标 vs 实际进展

| # | 你的需求 | 状态 | 证据 |
|---|---|---|---|
| 1 | 用户在 Manyfold 建 agent 时自动建 NarraNexus user | ✅ 完成 | POST /manyfold/agents 端点 + adapter.createAgent hook，已测真跑过：`mf_user_local_admin` 被自动创建 |
| 2 | 打开 agent chat 自动加载历史 | ✅ 完成（Manyfold 原生能力 + 我们 wire 通了）| Manyfold 按 agent 维护 session 列表，每个 session 有完整 message history；UI 打开 agent 时自动加载 |
| 3 | Native UI 可登录 + 配 provider | ✅ 完成 | http://localhost:18000 SPA 200，bin user 可看到自己 agent 和 3 个 provider 配置 |
| 4 | Manyfold 可以和 Agent 直接交互 | ✅ 完成 | 已经发送多条消息验证，agent 真回复 |
| 5 | "我在 18000 上能聊但 manyfold 不能" 调查 | ✅ 找到真因 | 不是 working_source 问题。**是 model 选择**：`anthropic/claude-sonnet-4-6` 经 NetMind Anthropic 转 Bedrock 时被 reject "invalid beta flag"。切到 `deepseek-ai/DeepSeek-V4-Pro` 立刻解决 |
| 6 | 你回来即可体验 | ✅ 一切就绪 | 见底部"打开 URL" 一节 |

---

## 你回来直接做的事

打开浏览器（**强刷 Cmd+Shift+R 清缓存**）：

### http://localhost:3002/    ← 主入口（Manyfold Web UI）

预期看到：
- 已自动登录为 admin (走 dev bearer token bypass)
- Agents 列表里有 **2 个 NarraNexus agent**：
  - **Demo Agent (via NarraNexus)** — creator=`bin`（手动 seed 的）
  - **Auto-Created Demo** — creator=`mf_user_local_admin`（API 自动建的）
- 点任一 agent → 看到 sessions 列表（之前的对话历史）→ 点 session → 看到所有消息 → 新发消息→ 真 chat 返回

### 测试样例（可直接抄到 UI 试）
- "Reply with exactly: pong" → 应回复 "pong" + chat tool 的 ack
- "你是谁?" → DeepSeek V4 Pro 生成介绍

---

## 关键真相：为什么 18000 上你能聊但 Manyfold 跑不通

**和 working_source 无关**。

我做了对照测试 (`tests/e2e/compare_paths.py`)：同一 agent，同一 user，分别走 `/ws/agent/run` (working_source=chat) 和 `/v1/chat/completions` (working_source=manyfold)：

- 用 `anthropic/claude-sonnet-4-6`：**两个都失败**（NetMind Anthropic 把请求转 AWS Bedrock，Bedrock 拒 claude-code CLI 自带的 beta header）
- 用 `deepseek-ai/DeepSeek-V4-Pro`：**两个都成功**

**你之前在 18000 上能聊**，应该是用过 OAuth 路径或别的 model（不是 claude-sonnet-4-6 经 NetMind）。

**长期建议**：NarraNexus 自己的 `Claude (OAuth via Bedrock)` provider preset 默认 model 配 anthropic/claude-sonnet-* 在 Bedrock 后端不可用。要么换 deepseek 系列，要么 NarraNexus 升级（自己绕开 claude-code beta flag）。这是 NarraNexus 本身的 bug，不在 Manyfold scope。

---

## 实际改了什么代码

### NarraNexus 仓 (`feat/manyfold` 分支)

| 文件 | 改动 | commit |
|---|---|---|
| `backend/routes/manyfold_agents.py` | 新增 `POST /manyfold/agents` 端点：建 user + agent + clone provider config | `7928522` |
| `backend/routes/openai_compat.py` | fallback content chunk 修正 (agent 跑了但没回复时也 emit 内容)  | `7928522` |
| `tests/e2e/compare_paths.py` | 路径对照测试驱动 | `7928522` |
| `pyproject.toml` / `uv.lock` | 加 `websockets` 测试依赖 | `7928522` |

**关键改动**：`_clone_provider_setup` 在 clone provider 时**重新生成 `provider_id`**，slot 用 old→new map 重写——这是必须的，因为 NarraNexus 的 provider_resolver 强制可见性检查（"provider 是别 user 拥有的拒绝服务"），直接复制 id 第一次 chat 就报 `LLMConfigNotConfigured`。

### Manyfold 仓 (`feat/narranexus-runtime` 分支，**绝不 push**)

| 文件 | 改动 | commit |
|---|---|---|
| `apps/api/.../bootstrap/narranexus.ts` | 上次已建 | `604c255` |
| `apps/api/.../bootstrap/k8s-framework-bootstrap.ts` | 加 narranexus union | `604c255` |
| `apps/api/.../orchestration/k8s-agent-orchestrator.ts` | pickBootstrap + imageForFramework switches | `604c255` |
| `apps/api/.../agents.module.ts` | provider 注册 | `604c255` |
| `apps/api/.../agent-runtimes/provisioning/k8s-container-provisioner.ts` | pickBootstrap switch 补 narranexus case | `4cf59dc` |
| `apps/api/.../agents/credentials/agent-credentials.service.ts` | pickK8sBootstrap switch 补 case | `4cf59dc` |
| `apps/api/.../chat/adapters/openclaw.adapter.ts` | model 字段：framework=narranexus 时用 `agents.internal_id`，openclaw 保持 `'openclaw'` | `4cf59dc` |
| `apps/api/.../chat/adapters/adapter-registry.service.ts` | narranexus → openclaw adapter 绑定 | `4cf59dc` |
| `apps/api/.../agents/adapters/narranexus-agent.adapter.ts` | listAgents 真去 hit `/manyfold/agents`；createAgent POST `/manyfold/agents` | `4cf59dc` |
| `apps/web/.../lib/chatAgents.ts` | 去掉 "Chat adapter pending for NarraNexus" 硬编码 block | `4cf59dc` |
| `apps/web/.../lib/agentCreate/frameworkOptions.ts` | 上次已建 | `604c255` |
| `docker-compose.yml` | postgres 5432→15432 避免端口冲突 | `4cf59dc` |

---

## 调试中发现并修复的 bug（如果不真测都会留给 Ying）

| # | bug | 修复 |
|---|---|---|
| 1 | 容器启动被 `_assert_local_bind_is_loopback` 拒 | 加 `ENABLE_MANYFOLD_API` / `RUNTIME_MODE=container` 两个 bypass |
| 2 | HEAD / 返 405（Manyfold preflight 需要 200）| 显式 `@app.head("/")` + SPA fallback 加 HEAD method |
| 3 | POST /v1 无 token 返 422 而非 401 | middleware 优先拦截 manyfold-class path |
| 4 | OFF mode `/manyfold/*` 被 SPA fallback 吃返 200 | SPA fallback 加 namespace guard |
| 5 | 前端 "Chat adapter pending for NarraNexus" 硬编码 | 删除 |
| 6 | reconciler 把我们 agent 标 stopped（adapter listAgents 返空）| 让 adapter 真去拉容器 /manyfold/agents |
| 7 | openclaw adapter 写死 model='openclaw' → 我们 endpoint 404 | framework=narranexus 时改用 agent.internal_id |
| 8 | clone provider 没换 provider_id → 可见性检查拒 | clone 时生成新 pid + slot remap |
| 9 | claude-sonnet-4-6 → NetMind → Bedrock "invalid beta flag" | 切 DeepSeek V4 Pro（你的建议）|

---

## 你可能想问的细节

**Q: chat 历史是 Manyfold 自己存的还是 NarraNexus 自己存的？**
A: 两边都存，各自独立。
- Manyfold `chat_sessions` + `chat_messages` 表存"这个 Manyfold user 跟这个 Manyfold agent 的对话"——UI 显示的就是这个
- NarraNexus `events` + narratives 存"这个 NarraNexus user (mf_xxx) 跟这个 agent (internal_id) 的所有事件"——agent 推理时的记忆从这里来

两者通过 `agents.internal_id` 双向映射。当 Manyfold 收到 chat 请求，转发到 NarraNexus 时带 `model=internal_id`，NarraNexus 知道用哪个 agent + 谁是 creator。

**Q: 那如果 user 在 Manyfold UI 跨 session 聊天，agent 还记得历史吗？**
A: **记得**。NarraNexus 的 narrative 按 user_id+agent_id 索引，session 切换不影响 agent 内部记忆。Manyfold session 只是 UI 上分组对话的方式。

**Q: 那 user 删了 Manyfold session 呢？**
A: Manyfold UI 上消失，NarraNexus narrative 还在。这是有意的——Manyfold 是"展示层"，NarraNexus 是"记忆层"。

**Q: 真上 K8s 会怎样？**
A: NarraNexusBootstrap.ts 已 ready。Ying 那边把 K8S_IMAGE_NARRANEXUS 配上 + cherry-pick `feat/narranexus-runtime` 两个 commit → 自动通。Mode A 测过的链路在 K8s 上唯一加的是真 pod 部署，protocol 层全部一致。

---

## 待你回来拍板的小事

1. **`NARRANEXUS_INHERIT_PROVIDER_FROM` env**：目前 Manyfold-side adapter 没默认值，要在生产环境 set 这个 env 才能 clone。本地 setup 时我直接在 POST body 里传了 `inherit_provider_from: "bin"`。生产可能：
   - 给一个固定操作员账户作为模板（如 `bin`）
   - 或要求每个用户在 Manyfold UI 第一次用时手动配
   - 或 NarraNexus 加个"系统默认 provider"机制（spec 里提过 `SYSTEM_DEFAULT_LLM_*`）

2. **`mf_<user_id>` 命名规范**：normalize 函数现在会把任何非字母数字字符替成 `_`，加 `mf_` 前缀。是否合理？

3. **NarraNexus claude-sonnet 经 Bedrock 失败**：这是 NarraNexus 自带 `NetMind (Anthropic)` preset 的真 bug——是否要本期一起修，还是单独提个 issue？

午饭吃好。
