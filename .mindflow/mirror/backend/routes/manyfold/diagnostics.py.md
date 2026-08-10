---
code_file: backend/routes/manyfold/diagnostics.py
stub: false
last_verified: 2026-08-10
---

# diagnostics.py — Manyfold 诊断面:容器健康 + per-agent pull 通道

## 2026-08-10 — per-agent pull 通道(观测性方案 A;mirror 补建)

本文件此前无 mirror(2026-05-25 的旧文件),本次补建。新增观测性
pull 半边(push 半边 = 日志 sink,两者**刻意零共享机制**——观测
通道自身坏掉时另一条还活着):

| 端点 | 数据源 | 回答 |
| --- | --- | --- |
| `…/agents/{id}/diagnostics/ingress` | `channel_trigger_audit`(#267 的托管生命周期行 + files-write 行,按 agent 索引跨渠道查) | "这条消息经历了什么" |
| `…/agents/{id}/diagnostics/events`(摘要)+ `/{event_id}`(全文) | `events` 表 | 列表只给生命周期字段 + 三列长度;**重内容(env_context/final_output/event_log)只按 id 单取**——"DB 只 pull"方针的实现形态 |
| `…/diagnostics/logs/services` / `logs/tail` | `NEXUS_LOG_DIR` 服务日志 | 免进沙盒的 tail(service/date/level/grep) |

## 设计决策

- **日志端点不按 agent 分**(日志是进程级):安全性来自注册条件——
  本 router 仅在 `ENABLE_MANYFOLD_API=1` 注册,sprite 是单用户沙盒,
  进程域 = 用户域。多租户部署(我方云)不启用此 API,天然不暴露;
- **读逻辑复用、鉴权不复用**:tail 借 `admin/logs` 的文件解析/
  seek-tail/level 过滤函数,但鉴权走 gateway token——`/api/admin`
  的 session 鉴权一字未动(方案 A 承诺);
- **脱敏是廉价保险,不是形式保证**:已知凭据键(context_token/
  bot_token/…)的 JSON 形态 + `Bearer <token>` 形态,正则替换后出门;
- **越权即 404**:`events/{id}` 按 `agent_id AND event_id` 查,别人
  的 event 与不存在的 event 不可区分(无存在性侧信道);
- 上限:行数 50/200、字段 512KB(带截断标记)、tail 2000 行;
  `grep` 是纯子串(无 ReDoS 面)。

## 上下游

平台侧零依赖(sprite ingress 透传)。生产工单场景的"找门"
(用户 → 沙盒 URL + token)待平台 resolver,见 todo §C 决策 3。
