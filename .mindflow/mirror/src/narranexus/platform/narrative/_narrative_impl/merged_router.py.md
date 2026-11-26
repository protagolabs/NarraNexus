---
code_file: src/narranexus/platform/narrative/_narrative_impl/merged_router.py
last_verified: 2026-11-25
stub: false
---

# merged_router.py — 合并路由的决策器:一次决策模型请求回答"续不续 / 去哪儿"

## 2026-11-25 — 决策者从 helper LLM 换成决策模型

Owner 决议:合并路径上的判决改由决策模型(`agent_framework.llm.decision_model`,
当前 `typesafe/jev-1.13`)作出,**速度优先于准确**——agent loop 里的
`switch_narrative` / `create_narrative` 是误归档的兜底。本路径**没有任何 LLM
调用,也没有 LLM 兜底**。原来的五选一 prompt(`prompts_merged.py`、
`build_merged_prompt`、`MergedRoutingOutput`、相关字面量与测试)已整体删除。

## 为什么存在

两次调用路径最多串行两次 helper LLM(prod 两次都付的轮次串行 p50 8,924ms,
非 LLM 部分均值 47.6ms)。合并成一次,再把这一次换成亚秒级、无长尾的决策模型。

## 三个单元,各自可单测

- `build_decision_request(inp)`:state = 本轮消息 + 上一轮(用户问 / agent 答;
  agent 主动发起时显式标 `agent_messaged_proactively`)+ 距上一轮分钟数;问题顺序:
  `anchor`(仅 `inp.offers_anchor`)→ `CANDIDATE_KINDS` 每一行(participant `p0..`,
  菜单 `m0..`)→ `no_topic`。每条线的呈现字段复用 `landings.candidate_labels`
  (所有路由候选的唯一字段选择,锚点也走它),菜单行再带 BM25 证据。共享规则只写
  通用规则(铁律 #4)。读侧上限经 `routing_blocks.clamp_head` 保头裁剪,被裁字段码
  进 `merged_truncated`。
- `verdict_from_scores(scores, offers_anchor, candidate_counts, thresholds)`:纯函数
  阈值规则。best_other(并列时按 `CANDIDATE_KINDS` 顺序——participant 先于菜单,
  P0-4——再按下标小者)≥ switch 且 **严格大于** 锚点分 → 该类的 verdict;否则锚点
  可问且 ≥ stay → continue_anchor;否则 no_topic ≥ 阈值 → no_topic;否则 new。
  刻意偏向留下(0.2 < 0.3,457 轮人工标注集上保住 92.0% 的真续接)。缺分数抛
  KeyError,不默认。阈值是 `DecisionThresholds`,`decide` 每次从 config 取。
- `decide(inp, endpoint)`:一次调用,永不抛、永不猜。组装失败 / 调用失败 / 分数不
  可用或越出契约 → `ok=False, verdict=failed`,日志文案区分"组装失败"与"调用失败"。
  成功日志行带全部原始分数——阈值可以离线用生产日志重调而无需重问模型(审计表没有
  能干净承载每候选分数的列,未新增列)。

**扩展点**:新增一种可切换的候选 = `CANDIDATE_KINDS` 加一行(verdict、key 前缀、
输入字段、展示 flag);问题构建、阈值规则、`allowed_verdicts`、越界检查、
`resolve_choice` 都按这张表走。key 里的序号就是对应输入列表的下标。

`decision_endpoint_for_turn()` 把叙事配置接到通用的凭据解析上(平台 key → 用户
同 host 的 provider key → None),由 `narrative_service.select()` 每轮调用;None
时该轮走两次调用路径。凭据以参数显式传给 `decide`,并发轮次不可能串 key。

## 设计决策

- **锚点问题无条件问**(只要可续):合并让外来线进入每一次决策,而续接轮里锚点
  BM25 零分的比例 8.2%–49.3%,它在不在问题集里不能是分数的函数。
- `continue_anchor` 与 `match` 是两个出口:"确认续接"不能被审计成一次换线。
- `allowed_verdicts` / `_contract_violation` 保留:阈值规则按构造不会越界,但落点
  绝不能拿到输入里不存在的下标。
- **失败不是答案**(rule 6):把失败读成"新话题"就是 D19 的形状。

## 上下游关系

- **被谁用**:`merged_select.select_merged`(`decide` / `resolve_choice` /
  verdict 常量)、`narrative_service.select`(`decision_endpoint_for_turn`)。
- **依赖谁**:`agent_framework.llm.decision_model`、`landings.candidate_labels`、
  `routing_blocks.clamp_head`、`config`(阈值 / 上限 / 决策端点)。
- **测试**:`tests/narrative/test_merged_decider.py`(三个单元各自)、
  `tests/narrative/test_merged_routing.py`(端到端落点、凭据、兜底)。

## 坑

`ask_yes_no` 在模块层导入,流程测试 monkeypatch `merged_router.ask_yes_no` 截住
网络边界——改成函数内 import 会让流程测试打真网络。
