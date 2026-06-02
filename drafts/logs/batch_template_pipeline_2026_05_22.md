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

---

## 更新 2026-06-02 — POC 收尾 + 4 个上线 + CI 修复

### Trigger
"差不多了,wrap up 一下,我们要换其他任务了。"

### Status
POC **完成**,pipeline 在两条独立源(OpenClaw SOUL.md / CrewAI YAML)上都打通,4 个模板已经在 narranexus-website `dev` 上线,Wave 2 的剩余路线(批量 + 反向生成)挂起到下一轮。

### 期间做了什么

1. **第二条源接通 — CrewAI**(2026-05-29)
   - 写了 `convert_crewai.py`(stdlib-only,带个手撸的小 YAML parser 处理 folded scalar)
   - 跑了 `marketing_strategy` + `recruitment` 两个 crew,各 4 agents → team bundle
   - 复用了 `nxbundle_lib` 同一套 primitives,证明抽象正确

2. **Wave 2 OpenClaw 精挑 5 个**(2026-05-29)
   - 跨 5 个类别:overnight-coder / sql-assistant / morning-briefing / travel-planner / phishing-detector
   - 3 个"纯 awareness"(无 skill),证明 pipeline 在最小载荷下也跑得通

3. **4 个上线到 website**(2026-06-02)
   - 跳过 `morning-briefing`(NN 已有 financial-morning-briefing)
   - 其余 4 个加到 `narranexus-website` `dev` `lib/templates.ts`,带:
     - rich `short_description` + `long_description` + `usage_tip`(从真实 awareness 抽提)
     - 文件 sha256 + size 计算
     - `manifest_summary`(agent count / skill count / requires_credentials)
     - **OpenClaw community attribution + MIT license 标注**(URL 指回原仓)
   - lint 0 errors,build 8 个 template pages 全过

4. **CI 修复**(2026-06-02)
   - dev 部署失败 —— EC2 上 dev branch 跟 origin 分叉,`git pull` 不会自动 reconcile
   - 把 `deploy-dev.yml` + `deploy-master.yml` 里的 `git pull origin <branch>` 改成 `git reset --hard origin/<branch>`
   - 自愈:新 workflow 从触发 commit 读,新 run 用新脚本就能跑通
   - 跟 2026-05-29 的"protagolabs = source of truth,recovery = reset --hard"政策对齐

### 关键产物位置

- **NarraNexus 仓** branch `feat/external-agent-import`(已推 protagolabs origin):
  - `scripts/external_agent_import/` — 5 个脚本 + 11 个 bundle + examples
  - 此 log 文件本身
  - REPORT.md 已 wrap-up 改版(版本号 + 状态 + 完整 inventory)
- **narranexus-website 仓** branch `dev`(只推 protagolabs origin,**不再双推 netmind**):
  - `lib/templates.ts` 新加 4 个 entry
  - `public/templates/<slug>.nxbundle` 4 个文件
  - `.github/workflows/deploy-{dev,master}.yml` CI fix

### 教训收获(已写入 memory)

- [[feedback-git-safety-checks]] —— commit/push 前 `git branch --show-current`;`git reset --hard <ref>` 是改当前分支不是改 ref
- [[feedback-remote-push-policy]](2026-05-29 update)—— 网站只推 origin,netmind 当 upstream 定时同步;不双推

### 留给下次的(parked)

- ▶ 全 199 OpenClaw + 14 CrewAI 批量化(可以一晚上跑完)
- ▶ Skill 自动配套(LLM 1-pass 按 awareness 内容选 3-5 个 VoltAgent skill)
- ▶ LLM rebrand Stage B 上线(hook 已留)
- ▶ **Final goal**: 从 skill 池反向生成 agent(原创但批量化)
- ▶ Runtime "Import from URL" UX

### 状态:POC 完成

下一个 session 直接接 parked 项,或者新任务。
