---
code_file: src/xyz_agent_context/repository/narrative_routing_audit_repository.py
last_verified: 2026-08-07
stub: false
---
# narrative_routing_audit_repository.py — 路由决策的落库审计（E1）

## 为什么存在

在这之前，narrative 选择的全部证据只进 `ProgressMessage` 和 loguru：
`selection_method`、闸门的分数依据、两个 LLM tier 的判断理由，一个字节都没到
数据库。docker 日志会轮转，grep 只找得到你想到要搜的词（事故教训 #5）。后果是
「路由准确率提升了 X%」没有分母，而 [[continuity.py]] 这一层——一次误判就能靠
`session.current_narrative_id` 连锁锁住若干轮——**完全不留痕迹**，真实误判率至今
未知。

## 非显然的设计约束：只存 ID 和分数是重放不出来的

两条独立原因，缺一条这张表就白建：

1. **BM25 的 IDF 和 avgdl 是在候选集自身上算的**（见 [[retrieval.py|memory 的
   bm25_rank]]）。某个候选的分数依赖池子里**每一个**其他文档，所以存 top-K 重放
   出来是另一组数。2026-08-07 实测：452 条真实本地 query，仅仅移除 8 条 default
   narrative 就让 top-1 翻转 9.7%、闸门决策翻转 5.8%。
2. **被打分的文本本身留不住**。`name + current_summary + description +
   topic_keywords` 被 [[updater.py]] 的异步 LLM 更新几乎每轮全量重写且不留历史，
   所以事后回读 `narratives` 表重建出的池子**是一个从未存在过的池子**——在它上面
   重放会得到自信但错误的数字。

所以审计存**整个池子**，每条指向一份内容寻址的文本快照。去重让代价可接受：相邻
两轮通常只有主 narrative 的摘要变了，100 条候选的池子每轮只新增约 1 行快照。

`tests/narrative/test_routing_audit.py::test_audit_replays_bm25_exactly` 钉住重放
性质——谁把池子裁成 top-K 或只存 ID，它就红。

## 坑

- **`record` 永不抛异常**：观察者不能弄坏被观察者。丢一行审计 ≠ 弄挂用户一轮对话。
- **`load_snapshots` 必须跳过 `None`**：`db.get_by_ids` 为了保持**输入顺序**会给
  查不到的 id **补 `None` 占位**（`db_backend_sqlite.get_by_ids` 的
  `result_map.get(id_val)`），而首次写入时每个 hash 都是 miss。第一版直接
  `r["text_hash"]` 下标，整批被 `record` 的 advisory except 吞掉 → **审计静默不写**
  ——正是这张表最不该有的失败模式。开发时就踩到了，测试钉住。
- 快照 hash 用 sha256 全长而非短摘要：这个 key 是审计行和它要精确复现的文本之间的
  唯一连接，碰撞会**静默污染重放**而不是让它失败。
