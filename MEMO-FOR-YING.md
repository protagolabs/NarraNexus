# Memo to Ying — Open Questions / Design Discussion from Manyfold ↔ NarraNexus Integration

在本地完整跑通（Mode A 含 Manyfold UI + 容器），下面列调试中冒出来的**架构 / 设计层**问题，等你回来一起拍

---

## 0. Manyfold runtime 启动时的 user 创建流程（用户 #4 问的）

### 现状

```
Manyfold UI user X 点 "New Runtime"
   ↓
Manyfold api 插 agent_runtimes 行（user_id=X，没 agent，没接触 NarraNexus）
   ↓
K8s orchestrator 起容器（pod 里跑 NarraNexus，里面 users 表是空的）
   ↓
                       ⚠️ 这里 NarraNexus 没有任何 user 被创建

随后用户操作分两条路：

【路径 A：通过 Manyfold UI 创建 agent】
   ↓ 触发 adapter.createAgent / adapter.addAgent
   ↓ POST /manyfold/agents 到容器
   ↓ NarraNexus 这边自动建 user `mf_<MANYFOLD_USER_ID>` (e.g. mf_user_local_admin)
   ↓ user_providers + user_slots 从模板 user (NARRANEXUS_INHERIT_PROVIDER_FROM env) clone 过来
   ↓ 建 agent 行 created_by=mf_<...>

【路径 B：通过 NarraNexus native UI 直接创建 agent】
   ↓ user 直接选 NarraNexus 自己的 user (例如 bin)
   ↓ NarraNexus 建 agent created_by=bin
   ↓ Manyfold 那边的 reconciler 拉 listAgents 看到 → 自动 insert Manyfold-side row
   ↓ 但 Manyfold 那边 agents.user_id 是 **runtime owner** (e.g. user_local_admin) 不是 bin
   ↓ Manyfold UI 无法显示 NarraNexus 真实 creator
```

### 含义

1. **NarraNexus 自带 multi-user**——可以有 native UI 用户 (bin) + 多个 Manyfold 映射用户 (mf_X, mf_Y) 并存在同一容器
2. **当前每个 NarraNexus user 独立 narrative + provider 配置**——他们的 agent 互相隔离
3. **Manyfold 永远只认 runtime owner**——所以即使容器内 NarraNexus 有 bin 的 agent，Manyfold UI 也展示成 user_local_admin 的

### 需要拍板

- **A. 现状**：单 Manyfold-runtime 对应单 Manyfold-user，NarraNexus 内部 multi-user 对 Manyfold 隐式。Native-UI 创建的 agent 在 Manyfold 显示但 owner 标签错（标 runtime owner）
- **B. 显式 user 映射**：listAgents 也返回 NarraNexus 真实 creator，Manyfold 那边显示"Created by bin (NarraNexus native)"小标签
- **C. 单 Manyfold runtime 强行单 NarraNexus user**：禁止 native UI 创建新 user，强制所有 agent 都用 `mf_<runtime_owner>`。简化但牺牲了 native UI 的多用户能力

**Bin 倾向 B**（信息透明但不限制功能），但需要 Ying 一起想。

---

## 1. NarraNexus vs Manyfold 的"会话"模型不对齐

### 现象

Manyfold 把每个 agent 的对话按 `chat_sessions` 切分，UI 侧栏一个 session 一条对话线。
NarraNexus 内部完全没有 `session` 概念——它有自己的 `narratives`（每个 agent + user 默认开 8 个 narrative slot，event 按 topic 自动路由进哪条 narrative）。

### 实测数据

用 demo_agent_001 通过 Manyfold UI 跑了 3 个 session 共 10 条消息后：

- **Manyfold side**：3 个 session 各自有自己的 message 列表（6 / 2 / 2 条）
- **NarraNexus side**：所有 9 个 event 全合并进同一个 `_N-01` narrative。NarraNexus 完全不知道 "Manyfold session" 是什么

### 含义

- ✅ **后端 agent 行为**：跨 Manyfold session 都记得历史。session A 说"我叫 Bin"，session B 问"我叫什么"，agent 答得出（因为 NarraNexus narrative 跨 session 累积）
- ❌ **UI 显示**：用户在 Manyfold 看 session B 看不到 session A 的消息列表。"打开 agent 看完整 creator-agent 交互"的用户预期，**UI 上做不到**

### 我们三个选项

| 选项 | 改动量 | 优劣 |
|---|---|---|
| **A. agent.framework='narranexus' 时强制单 session** | 改 Manyfold web/agents 渲染：narranexus agent 只显示最新一个 session，"New Session" 按钮隐藏 | 小改动；和 NarraNexus narrative 模型对齐；UX 直觉符合用户预期 |
| **B. NarraNexus events 同步进 Manyfold chat_messages** | 需要 backfill + 流式同步 | 复杂；两侧 mirror 维护成本高 |
| **C. 保持现状 + 加 UI 提示** | "this agent remembers across sessions" 一行字 | 最便宜；但用户继续会有困惑 |

**Bin **： 我觉得这里，NarraNexus Agent 会自动管理 session，也就是说 Agent 对 session 的划分只根据语意来，就算都在同一个 Manyfold 的session里，agent 也有可能创建多个 narrative；在不同的 session 里如果聊的内容不同，会自然转化成新的 narrative。所以我觉得我们这里可以保持现状，不需要做任何改动。

---

## 2. Manyfold 只记录通过 Manyfold 入口的交互

### 现象

`chat_messages` 表只存通过 `POST /api/agents/<id>/sessions/<sid>/messages` 走的内容。openclaw / hermes / claude-code / codex / gemini-cli 都靠 Manyfold 当唯一记忆中心（adapter 把 session history 截断 30 条全发给无状态的 agent）。

NarraNexus 是个**例外**：它有自己持久的 narratives，记 (user_id, agent_id) 维度，跨任意入口（Manyfold / native UI / Lark / Slack / Telegram）共享。

### 含义

如果用户**通过 NarraNexus native UI 直接聊** agent（绕开 Manyfold），那段对话：
- ✅ NarraNexus 知道 → agent 记得 → 下次 Manyfold 来问能用上
- ❌ Manyfold 不知道 → UI 上完全看不到，"Manyfold session 历史"会有缺口

反过来如果通过 Manyfold 聊：
- ✅ Manyfold 知道 → UI 显示完整
- ✅ NarraNexus 也知道 → agent 内部记忆也跟上

**问题**：UI 上的"已发生对话列表"对 NarraNexus 类 agent 来说是不完整的。和 #1 同源。

### 建议

感觉不是很关键的内容。

---

## 3. Agent 创建路径：Manyfold UI button vs 我们的 adapter

### 现象

用户在 Manyfold UI 点 "Create Agent" 选 NarraNexus framework：
1. Manyfold api 插 `agents` row（Manyfold-side）
2. K8s orchestrator → 部署 pod / 调 readiness probe
3. 部署完调 `NarraNexusAgentAdapter.createAgent` → 我们已经 wire 这一步会 POST `/manyfold/agents` 到容器内的 NarraNexus，**自动建 NarraNexus user + agent + clone provider 配置**

### 待你 review 的点

`POST /manyfold/agents` 接受一个**可选** `inherit_provider_from` 字段。我目前的实现：

- 通过 Manyfold 那边 `NARRANEXUS_INHERIT_PROVIDER_FROM` env 配一个**模板 user**（如 `bin`）
- 新 NarraNexus user 自动从这个模板 user clone 所有 user_providers + user_slots
- **关键**：clone 时**重新生成 provider_id** 并按 old→new map 重写 slot.provider_id（不然 NarraNexus 的 provider_resolver 可见性检查会拒——拒掉的报错是 `provider X not visible (owned by Y)`）

需要你定的：
- 这个"模板 user clone"机制可以接受吗？
- 或者你那边希望每个 Manyfold user 显式带 LLM key 入参（Manyfold credential management 那套）？
- 还是 NarraNexus 引入"系统默认 provider"机制（NarraNexus spec 里提过 `SYSTEM_DEFAULT_LLM_*`，没实现）？

---

## 4. Manyfold UI 上"Chat adapter pending for NarraNexus" 旧硬编码

### 现象

`apps/web/src/lib/chatAgents.ts:37-42` 写死：

```typescript
if (agent.framework === 'narranexus') {
    return { ready: false, reason: 'Chat adapter pending for NarraNexus.' }
}
```

是 narranexus 还在 `disabled: true` 占位时期的产物。

### 已修

本地分支 `feat/narranexus-runtime` 删了这个 block + 在 chat adapter registry 把 narranexus 绑到 openclaw adapter（协议契约一致）。等你 review。

---

## 5. NarraNexus 容器内多个 "name 一样的 agent"

reconciler 自动发现了我们 NarraNexus 里的 agent，**和我们手动 seed 的 Manyfold-side row 重复**：

```
agt_demo_narranexus            | Demo Agent (via NarraNexus) | ready
agt_agpgfajhknzadito4l4usybs6u | Auto-Created Demo           | running   ← 自动发现的
agt_auto                       | Auto-Created Demo (via Manyfold) | ready  ← 我手动 seed 的
```

后两个其实指向同一个 NarraNexus 内部 agent (`test_auto_agent_001`)。

### 建议

`agents` 表加唯一约束 `(runtime_id, internal_id)` —— 防止 reconciler + 手动 seed 双重插入。或者 reconciler 在插入前判 internal_id 已存在则 skip / merge。

---

## 6. NarraNexus Native UI

### 现象

NarraNexus 本地版也是需要 user 标记的，用户需要先创建自己的 user。
目前在 Manyfold 里，我们简化了这个过程，他点开 native ui 的时候自动根据他的 manyfold 的 user id 去创建了对应的 user 并且登录好。

但是这里也有可以优化的地方，user is 和 user 的 display name 什么的。

NarraNexus 的后端 API 中的功能都可以使用。


## 7. 不太相关但顺便提

### 7.1 `MF_K8S_GATEWAY_URL` 强制

Manyfold api 启动时强 require 这俩 env。本地纯 Mode A 测试用不到，我塞了 dummy 值绕过。

如果你想本地开发体验更顺，考虑：
- 默认值（`http://localhost:18000` 之类的 placeholder）
- 或加 `MF_K8S_GATEWAY_DISABLED=1` env 让 service 跳过初始化

### 7.2 `@manyfold/docs` 强制 Node 22+

`apps/docs` 用 Astro 要 Node 22+，而其他 app 在 20 也跑。`pnpm dev`（turbo dev）整组挂掉。我用 `pnpm --filter <api/admin/web> dev` 各起绕开。

### 7.3 docker-compose.yml postgres 端口 5432 占用

你 dev 机如果装了 postgres 服务，会跟 docker compose 的 postgres 冲突。本地我改了 docker-compose.yml 用 15432。

---

## 已完成的事 (供你 review)

- `feat/narranexus-runtime` 本地分支：7 个文件改 + `narranexus.ts` bootstrap 新建。**绝不 push**，等你 review
- NarraNexus 仓 `feat/manyfold` 分支：`POST /manyfold/agents` 端点 + adapter 改 + Dockerfile + spec 文档。已 commit 没 push
- 完整工作日志：`reference/self_notebook/2026-05-26-lunch-session-report.md`
- 设计文档：`reference/self_notebook/specs/2026-05-22-docker-cloud-agent-design.md`

期待回来一起 review 这些问题，特别是 #1 (session 模型) 和 #4 (provider inheritance)。

— Bin
