---
code_file: src/xyz_agent_context/narrative/config.py
last_verified: 2026-08-16
stub: false
---

## 2026-08-16 — NARRATIVE_DEFAULT_BUCKETS_ENABLED（C-1 default 桶治理）

八条播种叙事（GreetingAndCourtesy…）**不再是路由容器**。开关为 False（出厂值）
时它们退出 BM25 池、退出 judge 候选菜单、不再为新 (agent,user) 播种；八个类目名
**保留在 judge 的 instructions 里当词表**——"这一轮没有可沉淀的话题"这个判断仍然
用它们表达，只是不再指向一行可以把 event 塞进去的记录。

为什么必须动（实测，spec `2026-08-14-default-bucket-governance-design.md`）：
prod 用户轮 **26.4%**、prod 真实切片 chat 轮 **27.0%** 的主叙事是桶，而全库
**9080/9080** 条桶的摘要至今是出厂模板——桶永远不积累检索面，进去的话题**再也召
不回来**。另外这八行只是待在池子里就扰动了 **9.7%** 的 top-1（IDF/avgdl 按传入集
合算）。

开关默认 False = 新行为；置 True 整体回滚（同装置 before/after 对照用）。两种取值
下**都不删除任何存量行**（铁律 #6）——只是路由不再看见它们。
## 2026-08-14 — 撤回单位门：持锚点不创建是实测终案（supersede 下一条）

单位门（8 units）上线前实测被否：中文正常延续句 BM25 top-1 落在 1.0-3.2
（骑在 RAW_FLOOR=3.0 上），≥8 单位的纯确认句（「嗯嗯我明白了那就这样吧」）
probe 出来就是沉默——一段连贯 7 轮中文对话被打成 5 条 narrative（40 字符
门下同对话=1 条）。BM25 在 CJK 上无法区分「新话题」与「省略式延续」，
错误不对称定案：错归档可恢复（下一个 full turn continuity 重路由、
switch_narrative 在），碎片化不可恢复（永久分裂+agent 半途拿空历史）。
故 `FAST_NEW_THREAD_MIN_QUERY_UNITS` 删除，持 live 锚点时 fast 路径一律
不创建；完整数据与新线三来源写在 FAST_ANCHOR_OVERRIDE_FLOOR 的 NOTE。

## 2026-08-14 — 新线门改按语言单位计数（R2 复核 I3）

`FAST_NEW_THREAD_MIN_QUERY_CHARS`(40 字符) → `FAST_NEW_THREAD_MIN_QUERY_UNITS`
（默认 8，env `NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_UNITS`）。字符计数对
CJK 全盲：中文完整句 11-15 字符，40 字符门对 zh 用户几乎永不打开——锚点
仍吞掉一切换题（正是要修的症状）。单位=CJK 每字 1（汉字/假名/谚文约各
承一词）+ 其余按空白分词每词 1（`narrative_service.query_units`），中英
同尺。残余偏置对所有语言一致声明：低于 N 单位的新话题句先留旧线程（如
6 词英文短命令），等更完整消息再开新线。默认值是临时校准，待
gate_top1_raw 数据落地后重调。

## 2026-08-14 — FAST_NEW_THREAD_MIN_QUERY_CHARS（#307 增量 🟡1）

fast 路径开新线的长度门（默认 40，env
`NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_CHARS`）：锚点在手时只有「BM25 全面
沉默 + query 长到沉默可信」才 create。依据即本文件 RAW_FLOOR 注释里的实
测（<40 字符中位 top1 ~5.3）：短省略句零重叠属常态、不可当新话题证据；
完整句子零重叠=真新话题。已声明的残余偏置：短的新话题句会先留在旧线程，
等更完整的消息再开新线——一次错归档好过每个"ok"开一条线。不是时间窗。

## 2026-08-14 — FAST_ANCHOR_OVERRIDE_FLOOR（#307 🟡1/🟡2）

新阈值：fast 路径持有 live 锚点时 BM25 抢线所需的强分下限（默认 12.0，
env `NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR`）。刻意高——raw BM25 随 query
长度伸缩（<40 字符中位 ~5.3），短跟进句永远抢不了线、留在原线程；长而
主题明确的消息可以切。与 RAW_FLOOR（噪声滤网）职责不同。同时在
2026-05-20 session 永不超时 NOTE 旁补注：fast 路径同守此规（曾短暂引入
30 分钟窗，同日删除）。

## 2026-07-29 — 高置信判据换成 RAW_FLOOR + MARGIN_RATIO

删除 `NARRATIVE_MATCH_HIGH_THRESHOLD = 0.70`。它比的是 squash 后的
`s/(s+1)`，等价于原始分 2.33，在中文单字 unigram 下几乎恒真——273 条真实
prod 轮次实测短路 87.5%。换成 `NARRATIVE_MATCH_RAW_FLOOR = 3.0` +
`NARRATIVE_MATCH_MARGIN_RATIO = 2.0`，同一批数据短路率降到 48.0%。

**RAW_FLOOR 是噪声过滤不是强度测试，别调高**——原始分随 query 长度涨，高
floor 会毙掉短追问。定值依据和取舍全写在 [[routing_gate.py]]。改这两个常量
会让 `tests/narrative/fixtures/routing_cases.json` 的期望值失效，必须一起重算。

> 2026-05-29：删除全部 `EVERMEMOS_*` 常量（EverMemOS 整体移除）。Narrative
> 检索现在无条件走本地 VectorStore，没有外部检索后端开关。

# config.py — Narrative 系统所有可调参数的中央控制台

## 为什么存在

Narrative 检索、连续性判断、embedding 更新是计算密集型操作，各阶段的阈值直接影响系统的记忆质量和 API 成本。把所有参数集中在一个单例对象里，有几个好处：实验调参时只需改一处；文档注释就在代码旁边，解释每个参数的含义和推荐范围；生产与开发环境可以通过替换此对象的字段来切换行为，不需要散落在各处的 if/else。

## 上下游关系

**被谁用**：`_narrative_impl/retrieval.py` 读 `NARRATIVE_MATCH_HIGH_THRESHOLD`、`NARRATIVE_SEARCH_TOP_K`、`EVERMEMOS_*` 系列参数；`_narrative_impl/updater.py` 读 `NARRATIVE_LLM_UPDATE_MODEL`、`EMBEDDING_UPDATE_INTERVAL`；`_narrative_impl/continuity.py` 读 `CONTINUITY_LLM_MODEL`；`_event_impl/processor.py` 读 `MAX_RECENT_EVENTS`、`MAX_RELEVANT_EVENTS`；`session_service.py` 读 `SESSION_TIMEOUT`。

**依赖谁**：无外部依赖，纯 Python 类。文件末尾导出单例 `config = NarrativeConfig()`，调用方通过 `from .config import config` 获取。

## 设计决策

所有参数都有行内注释解释推荐值、调参建议和适用场景，这是刻意的——这个文件就是系统的"调参手册"，不依赖外部文档。

`NARRATIVE_LLM_UPDATE_INTERVAL` 2026-07-24 起就在这里（NarrativeConfig 类属性）——包根那个只剩单常量的全局 config.py 已删除，narrative 是它唯一的消费者，「全局/局部」的历史分工不复存在。

`EVERMEMOS_ENABLED = False` 现在是默认值——云端部署目前没有运行 EverMemOS 服务，开着会让 backend 在每次 hook 写入时打 ConnectError。打开前先确保 EverMemOS 服务已经跑起来；retrieval.py 在禁用时直接走纯向量检索路径，不会触碰 HTTP 客户端。配套的 belt-and-suspenders 在 `utils/evermemos/client.py:get_evermemos_client` 里——禁用时返回 no-op stub，覆盖那些没显式 gate 的调用方。

`ENABLE_HIERARCHICAL_STRUCTURE = False` 和 `ENABLE_AUTO_SPLIT = False` 是 Phase 2 预留的功能开关，目前代码里没有对应实现，改成 True 没有效果。

## Gotcha / 边界情况

`VECTOR_SEARCH_MIN_SCORE = 0.0` 意味着向量搜索没有最低分过滤，所有 Narrative 都会进入候选集再由 LLM judge 裁决。这是有意设计的，用宽松召回 + 精准 LLM 判断替代严格阈值过滤，但候选集如果很大（几百条 Narrative），LLM judge 的 prompt 会很长。如果发现 LLM judge 超出 token 限制，可以调高这个值做初步过滤。

`EMBEDDING_MODEL = "text-embedding-3-small"` 修改后需要重新生成所有历史 embedding，因为新旧模型的向量空间不兼容，直接混用会导致语义检索结果完全错乱。有专门的 `EmbeddingMigrationService` 处理这种迁移，但需要手动触发。

## 新人易踩的坑

`MAX_NARRATIVES_IN_CONTEXT = 3` 控制的是 select() 返回的 Narrative **数量上限**，不是每条 Narrative 注入多少事件。事件数量上限由 `MAX_EVENTS_IN_CONTEXT = 6` 控制，这两个数字容易混淆。
