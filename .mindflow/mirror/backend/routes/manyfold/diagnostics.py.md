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
| `…/agents/{id}/diagnostics/events`(摘要)+ `/{event_id}`(全文) | `events` 表 | 列表只给生命周期字段(**SQL 列投影**,重内容列根本不出库);全文只按 id 单取——"DB 只 pull"方针的实现形态 |
| `…/diagnostics/logs/services` / `logs/tail` | `NEXUS_LOG_DIR` 服务日志 | 免进沙盒的 tail(service/date/level/grep) |

## 设计决策

- **日志端点不按 agent 分**(日志是进程级):边界是 **runtime 而非
  用户**——gateway token 是运营方凭据,持有者经 files API 本就可读写
  本 runtime 内**所有** agent 的 workspace(含 manyfold/agents.py +
  backend/auth.py 明确支持的多 Manyfold 用户形态),进程级日志不扩大
  既有暴露面。初版"sprite = 单用户"论证与仓库代码矛盾,review 纠正;
- **读逻辑复用、鉴权不复用**:tail 借 `admin/logs` 的文件解析/
  seek-tail/level 过滤函数,但鉴权走 gateway token——`/api/admin`
  的 session 鉴权一字未动(方案 A 承诺);
- **脱敏是廉价保险,不是形式保证**:已知凭据键(context_token/
  bot_token/…)的 JSON 形态 + `Bearer <token>` 形态,正则替换后出门;
- **越权即 404**:`events/{id}` 按 `agent_id AND event_id` 查,别人
  的 event 与不存在的 event 不可区分(无存在性侧信道);
- **排序/截断/投影全部下推 SQL**(review Critical):`db.get` 的
  limit/order_by/fields;facade 此前丢弃 fields 参数(三个后端都支持),
  本批补透传。铁律 #14 的数十小时 run 正是 event_log 最大的场景——
  诊断端点绝不能成为打死被诊断容器的那个查询;
- `since` 双侧经 `_norm_time` 归一(复用仓库 `_event_time_str` +
  T→空格折叠):T 形 ISO 与空格形存储的字典序比较曾静默返回空;
  回归测试走真实写入路径(repo.append)而非手写字符串;
- **先脱敏后截断**(`_redact_clip`):凭据恰跨截断点时闭合引号被切,
  key-pattern 正则失配、token 前缀漏出——顺序即漏洞;
- 上限:行数 50/200、字段 512K **字符**(CJK 下 ≈1.5MB 字节,命名
  `_MAX_FIELD_CHARS` 如实)、tail 2000 行;`grep` 纯子串(无 ReDoS)。

## 上下游

平台侧零依赖(sprite ingress 透传)。生产工单场景的"找门"
(用户 → 沙盒 URL + token)待平台 resolver,见 todo §C 决策 3。
