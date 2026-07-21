---
code_file: scripts/manyfold_trigger_experiment/fake_manyfold.py
last_verified: 2026-07-20
stub: false
---

# fake_manyfold.py — 触发面外移验证的"假 Manyfold"

## 为什么存在

要验证"trigger 走 Manyfold"(触发面外移)是否成立,但本地无法 1:1 复刻
Manyfold(Firecracker VM / sprites / 空闲挂起 / automations 引擎)。按"抽象
逻辑一致即可",这个脚本只扮演 Manyfold 从挂起沙盒手里接管的两件事——"耳朵"
(IM 接收)和"钟"(定时)——用 NarraNexus **真实暴露的 HTTP 契约**去驱动一个
本地 gated 模式的 Nexus,验证整条事件流闭合。

## 复刻的契约(全部 gateway-token 鉴权)

- `pull-jobs` / `pull-channels` → `GET /manyfold/jobs` / `/manyfold/channels`
  (只读 inventory,Manyfold pull 后自己做镜像)。
- `fire-job` → `POST /v1/chat/completions`,content 严格为
  `[[nx:run_job <id> v1]]`(镜像闹钟到期),流式读回执行结果。用 stream 是
  因为 run-job dispatch 每 15s 发心跳,长 run(目标 job + bounded drain)不会
  撞客户端/代理 read timeout。
- `send-im` → `POST /v1/chat/completions` 带 `channel_provider` +
  `channel_context`(模型 B 的入向)。断言 agent 用**本地**渠道工具
  (`lark_cli` 等)回到对的 room,而非经 `send_message_to_user_directly` 由
  平台投递(那是模型 A)。
- `serve-notify` → 收 config-change webhook(`{runtimeId, kinds}`),证明
  Nexus 侧写配置后会 notify。

## 上下游

- 打的是 `backend/routes/openai_compat.py`(`/v1/chat/completions` +
  `build_inbound_run_context` 的模型-B 入向)与 `manyfold_sync.py`(只读端点、
  run-job dispatch、notify webhook)。
- 配套 `seed_experiment.py` 先塞测试数据(agent 复用现成的、stub lark 凭证、
  到期 job)。
- 需要 Nexus 以 gated 模式起(`NEXUS_EXTERNAL_TRIGGERS` 语义:不起
  job_trigger / channel_triggers;起 poller / message_bus / mcp / backend)。

## 设计决策 / Gotcha

- **纯 HTTP,不接真 bot**:第一阶段只断言 agent **发出**了打向对的 room 的
  本地工具调用(抽象逻辑)。真发送要真 bot 凭证——用假凭证时 agent 会调
  `lark_cli` 但 Lark API 拒绝,随后兜底走一条解释性 `send_message`;这属预期,
  不影响"回复路由对不对"的判定。
- `send-im` 用非流式(等完整 run);`fire-job` 用流式(心跳撑住长 run)。
- 只是实验/验证脚手架,不在生产路径,不被任何后端代码 import。
