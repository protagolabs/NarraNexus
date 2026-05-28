# 批量生产 Templates · Pipeline 探索 — 2026-05-22

- **Trigger**: 用户想"批量生产 templates"。现在 templates 靠我们自己手工搭,产量起不来。想从公开 agent 仓库(如 Claude Code subagents + skills)批量转成 NarraNexus 模板。要 PRD + early exploration。
- **Branch / commit**: main(无代码改动,仅新增设计文档)
- **Status**: PRD v0.1 已落档(`reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md`),等 Bin哥 review + 拍首批参数

## 核心结论(v0.1)

1. **SKILL.md 跟 Claude Code skills 格式完全一致**(Anthropic 2025-10 引入、2025-12 开源)。零迁移成本。
2. **公开 subagent / skill 仓库存货巨大**:VoltAgent skills 1000+、wshobson 191 agents + 155 skills + 102 commands、0xfurai 100+ subagents。
3. **NarraNexus bundle 几乎全机械可生成**:`manifest.json` + `agent.json` + `awareness.json` + 5 个 module 的 instance stamp + `workspace.tar.gz`。
4. **映射干净**:Claude Code subagent frontmatter → `agent.json`,body → `awareness.json`,`.claude/skills/*` → `workspace.tar.gz:skills/*`。
5. **推荐三 Phase**:Phase 1 单 agent 机械批转 → Phase 2 skills 自动配套 → Phase 3 多 agent 团队。

## 取证记录(v0.1)

- bundle 实证:`~/Downloads/marketing_team-20260527 (1).nxbundle`
- 公开仓库 survey:PRD §3.3 + Sources(8 个仓库 + Anthropic 官方 docs)
- Claude Code subagent 格式:`code.claude.com/docs/en/sub-agents`
- NarraNexus 工具集 / 安全策略:`xyz_claude_agent_sdk.py:316`、`_tool_policy_guard.py`

---

## 更新 2026-05-28 — 主线切换到 OpenClaw + POC 验证通过

### 触发

Bin哥反馈:原始计划是直接复制**完整 agent**(不是 subagent),例如 OpenClaw 这种 ecosystem,他们有 agent 设定、skill 等等,跟我们更像。另外想看有没有现成 agent team。

### v0.2 核心变化

**主线从 Claude Code subagents 切到 OpenClaw SOUL.md**:

1. **OpenClaw 是真项目且 ecosystem 重叠度高**:`openclaw/openclaw` 375k stars,自托管多渠道 personal/team AI assistant,与 NN 定位平行。
2. **`mergisi/awesome-openclaw-agents`(MIT)有 199 个 SOUL.md**,覆盖 24 类。`agents.json` 是现成索引(id/category/name/role/path/deploy)。
3. **SOUL.md 比 Claude Code subagent 还简单 —— 纯 Markdown,无 frontmatter**。整段塞 `awareness.json` 即可。
4. **VoltAgent agent-skills(1000+)标 OpenClaw / Claude Code / Codex / Gemini / Cursor 全兼容**,SKILL.md 标准与 NN 完全一致,零迁移。
5. **自动 team 检测信号真实**:Orion SOUL.md 写 "Works best with Echo and Radar",`agents.json` 里跨类真有 echo(marketing)和 radar(business)→ 可机械聚类成 team。
6. **新增 rebrand pass**:Stage A regex 替换 OpenClaw 平台引用 → NarraNexus;Stage B(可选)LLM 精修。

### POC 验证结果

代码 `drafts/batch_template_pipeline/code/convert_soul_to_nxbundle.py`(stdlib only, ~280 行)。

输入 `drafts/batch_template_pipeline/input/orion_SOUL.md`(2063B,从 awesome-openclaw-agents/agents/productivity/orion/SOUL.md cached)。

跑出:`drafts/batch_template_pipeline/output/orion.nxbundle`(5990 字节,12 个文件,结构与真 nxbundle 一致):

```
manifest.json (含 source_attribution)
bus.json / inbox.json / mcp_hints.json (empty)
agents/agent_fd182d93aa03/
  agent.json
  awareness.json  ← "powered by NarraNexus" ✓ (rebrand 已替换)
  workspace.tar.gz (88B placeholder)
  instances/{Awareness,BasicInfo,Chat,SocialNetwork,MessageBus}Module/*.json
```

Rebrand 验证 — bundle 里 `"powered by OpenClaw" in body == False`,`"powered by NarraNexus" in body == True`。✓

### Importer 最小要求(实证 `bundle/importer.py`)

- **硬要求**:`manifest.json` + `agents/<id>/agent.json`
- **功能性必需**:`agents/<id>/awareness.json`
- **全部可选**(`.exists()` 门控):narratives / instances / agent_messages / jobs / artifacts / rag / bus / mcp_hints / workspace.tar.gz

### 取证记录(v0.2)

- OpenClaw 验证:`gh repo view openclaw/openclaw` → 375k stars / 78k forks / 今日仍在推
- 模板仓库:`mergisi/awesome-openclaw-agents` 3.4k stars, MIT, 199 entries in agents.json
- SOUL.md 格式实证:抓 `agents/productivity/orion/SOUL.md`,纯 markdown 确认
- Skills 源决策:`mergisi/awesome-openclaw-agents/skills/` 只有 10 个(claude+gemma 各 5),真正大池是 `VoltAgent/awesome-agent-skills`(1000+,多框架兼容,标 SKILL.md 标准)
- Importer 最小要求:`src/xyz_agent_context/bundle/importer.py:100,750,867`(线号实证)
- POC 代码 + 输入 + 输出:`drafts/batch_template_pipeline/`

### Next step

**未跑通**:POC 还没在真 NarraNexus dev 里 import 验证可聊天。需要 Bin哥起本地后端跑一次 `bundle/importer.py` dry-run + import。

**等 Bin哥拍板**:
1. 首批目标数(50?100?)
2. 类别选择(优先 marketing + development + business + creative 前 4 大类共 71 个,还是均匀)
3. Skills 默认怎么配
4. 模板页 UI 加 source attribution
5. CrewAI parallel 是否同步开
6. Stage B LLM rebrand 何时上

**我并行可推**:
- Task #56 ✅ done
- Task #55 ✅ POC done(本次)
- Task #57 ✅ SOUL.md 调研 done
- Task #58 ✅ rebrand 规则 done(Stage A 上线,Stage B 留 hook)

### 已开任务(状态)

- #53 ✅ Draft PRD v0.1
- #54 ✅ Drop session log
- #55 ✅ POC converter(本次跑通)
- #56 ✅ Verify importer minimum bundle
- #57 ✅ Investigate SOUL.md + skills
- #58 ✅ Build rebrand pass

### PRD 更新

`reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md` 已升级到 v0.2,主线、字段映射、Phase 进度、POC 结果、备选方案排序、风险、开放问题全更新。
