# Status · Template Pipeline POC

**Date**: 2026-05-28
**Branch**: `feat/external-agent-import`
**Latest commit**: `c773c68` (OpenClaw scaffold) — CrewAI extension pending second commit

---

## 进度

**Source 1 · OpenClaw**(`mergisi/awesome-openclaw-agents`,MIT,199 SOUL.md / 24 类)→ **4 个模板**:

- **3 单 agent**:Orion(coordinator, baseline)、**Lens**(code reviewer + 3 skills)、**GitHub PR Reviewer**(+ 1 skill)
- **1 auto-detected team**:**Coordinator Trio**(Orion + Echo + Radar,跨 3 大类,带 3 skill)

**Source 2 · CrewAI**(`crewAIInc/crewAI-examples/crews`,MIT,16 个现成多 agent crew)→ **2 个 team 模板**:

- **Marketing Strategy Crew**(4 agents:Lead Market Analyst / Chief Marketing Strategist / Creative Content Creator / Chief Creative Director;5 tasks)
- **Recruitment Crew**(4 agents:Researcher / Matcher / Communicator / Reporter;4 tasks)

Pipeline 端到端跑通 —— Orion 已在 cloud 验过 import + 对话。**两套源已确认机械化可行**(SOUL.md / agents.yaml 两条线都跑通)。

| 源 | 格式 | 量 | 已产出 | 与 NN 适配 |
|---|---|---|---|---|
| OpenClaw SOUL.md | 纯 markdown | 199 / 24 类 | 4 bundle | ⭐⭐⭐⭐⭐ |
| CrewAI YAML | agents.yaml + tasks.yaml | 16 crews | 2 bundle | ⭐⭐⭐⭐⭐ |
| VoltAgent skills | SKILL.md (Anthropic 标准) | 1000+ | 已用于 Lens | ⭐⭐⭐⭐⭐ |

## Next

**A. 把现成的批量跑起来**:OpenClaw 全 199 个 + CrewAI 剩下的 14 个 crew(`stock_analysis`、`trip_planner`、`game-builder-crew`、`instagram_post`、`landing_page_generator`、`job-posting`、`screenplay_writer`、`recruitment` 等)→ 加上 skill 自动配套和人工 review,可以快速堆到 50-100+ 模板。

**B. 单仓库 multi-agent team(待定)**:原计划的 MetaGPT / ChatDev 是 2023 老项目、agent 配置在 Python 代码里硬编码,跟我们路线不太对路 —— **暂搁置**。我同步搜了"近期 + cc 同源"的"单仓库 team"项目,**没找到合适的**(2026 年主流模式是大集合,我们已经覆盖了 OpenClaw / VoltAgent / wshobson)。这条线**保留 watch,但不阻塞主线**。

**C. Final goal — 从 skill 池反向生成 agent**:用 VoltAgent 的 1000+ skill 池,按主题聚合 + LLM 编织出 agent 身份,产出**原创但批量化**的模板(不再依赖外部 agent 库)。

---

**产出位置**:`scripts/external_agent_import/` —— 4 个脚本 + 6 个 bundle。详见 [`README.md`](./README.md)。
