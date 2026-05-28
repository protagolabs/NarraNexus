# External Agent Import — POC Pipeline

Convert publicly-available agent configs (OpenClaw SOUL.md, CrewAI YAML, …) into NarraNexus `.nxbundle` templates. **Batch produces templates so we don't author them by hand.**

> Status: **POC / exploration**. Pipeline proven end-to-end on OpenClaw → 4 bundles produced + 1 cloud-imported. CrewAI + popular single-repo agent teams are next.
> Design doc: [`../../reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md`](../../reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md)
> Log: [`../../drafts/logs/batch_template_pipeline_2026_05_22.md`](../../drafts/logs/batch_template_pipeline_2026_05_22.md)

## Layout

```
scripts/external_agent_import/
├── README.md                    # this file
├── nxbundle_lib.py              # shared primitives: rebrand, stamps, workspace tar, bundle ZIP
├── convert_single.py            # CLI: one SOUL.md -> single-agent .nxbundle
├── convert_team.py              # CLI: team spec JSON -> multi-agent .nxbundle
├── auto_team_detect.py          # CLI: scan SOUL.md cross-refs -> cluster candidates
├── _v0_legacy_convert_soul.py   # first POC iteration (kept for reference)
├── examples/                    # POC inputs + detector output
│   ├── orion_SOUL.md            # cached OpenClaw source
│   ├── team_coordinator_trio.json
│   └── detected_teams.json
└── bundles/                     # produced .nxbundle artifacts
    ├── orion.nxbundle               5.8 KB  · single agent · no skills (v0)
    ├── lens.nxbundle                12 KB   · single agent · 3 skills
    ├── github-pr-reviewer.nxbundle  8.6 KB  · single agent · 1 skill
    └── coordinator_trio.nxbundle    22 KB   · team of 3 · 3 skills · auto-detected
```

## Quick start — reproduce from scratch

Prerequisite: `git clone https://github.com/mergisi/awesome-openclaw-agents.git /tmp/awesome-openclaw-agents` (the SOUL.md + skills source).

```bash
cd scripts/external_agent_import

# 1) Auto-detect teams from cross-refs in 199 SOUL.md files
python3 auto_team_detect.py \
    --repo /tmp/awesome-openclaw-agents \
    --top 30 \
    --out examples/detected_teams.json

# 2) Build a skill-laden single agent
python3 convert_single.py \
    --soul /tmp/awesome-openclaw-agents/agents/development/code-reviewer/SOUL.md \
    --name "Lens" \
    --role "Code reviewer and quality gatekeeper" \
    --category development \
    --source-path agents/development/code-reviewer/SOUL.md \
    --skill-dir /tmp/awesome-openclaw-agents/skills/claude/git-commit-writer:git-commit-writer \
    --skill-dir /tmp/awesome-openclaw-agents/skills/claude/excalidraw-architecture:excalidraw-architecture \
    --skill-dir /tmp/awesome-openclaw-agents/skills/claude/cost-optimizer:cost-optimizer \
    --out bundles/lens.nxbundle

# 3) Build a team bundle from a JSON spec
python3 convert_team.py \
    --team-spec examples/team_coordinator_trio.json \
    --out bundles/coordinator_trio.nxbundle
```

## What the pipeline does

```
agents.json (master index)
  │
  ├─ fetch SOUL.md per entry
  ▼
parse: pure markdown body (no frontmatter)
  │
  ├─→ rebrand pass: OpenClaw → NarraNexus (regex; LLM hook pluggable)
  │
  ├─→ generate agent_id, instance stamps (5 modules),
  │   awareness.json (rebranded body), agent.json
  │
  ├─→ tar packaged skills into workspace.tar.gz under skills/<name>/
  │
  └─→ ZIP into .nxbundle (manifest + agents/ + bus.json + inbox.json + …)
```

**Importer minimums verified** (`src/xyz_agent_context/bundle/importer.py:100, 750, 867`):
- Required: `manifest.json` + `agents/<id>/agent.json`
- Functional: `awareness.json`
- Optional (`.exists()` gated): narratives / instances / agent_messages / jobs / artifacts / rag / bus / mcp_hints / workspace.tar.gz

## Source attribution & license

All produced templates carry `manifest.source_attribution`:

```json
{
  "agent_id": "...",
  "source": { "repo": "github:mergisi/awesome-openclaw-agents",
              "path": "agents/<cat>/<id>/SOUL.md",
              "license": "MIT" },
  "rebrand_diffs": ["powered by OpenClaw -> powered by NarraNexus", ...]
}
```

OpenClaw `awesome-openclaw-agents` is MIT licensed. Templates published on `/templates` should display source repo + original author credit.

## Next phases

| Phase | Status | Notes |
|---|---|---|
| 0  Importer minimum verified | ✅ | `bundle/importer.py:100, 750, 867` |
| 0.5  POC single agent (Orion) | ✅ | Cloud import verified |
| 1  Single agents + skills + auto-team detect | ✅ POC done | 4 bundles produced; ready to batch all 199 |
| 1.5  CrewAI crews → team bundles | 🔍 surveying | YAML format: `agents.yaml` + `tasks.yaml` (role/goal/backstory) |
| 1.6  Popular single-repo agent teams | 🔍 surveying | MetaGPT (PM/architect/eng/QA), ChatDev (virtual software company), …  |
| 2  Skill-driven agent generation | 📋 planned | Use VoltAgent's 1000+ skills as basis; LLM stitches identity around skill bundles |
| 3  LLM rebrand Stage B + head-of-pack polish | 📋 planned | Heavy templates get LLM rewrite for fully NarraNexus-aware prose |

## See also

- PRD: [`reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md`](../../reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md)
- Session log: [`drafts/logs/batch_template_pipeline_2026_05_22.md`](../../drafts/logs/batch_template_pipeline_2026_05_22.md)
- Upstream: [`mergisi/awesome-openclaw-agents`](https://github.com/mergisi/awesome-openclaw-agents) (MIT, 199 SOUL.md, 24 categories)
- Skills pool: [`VoltAgent/awesome-agent-skills`](https://github.com/VoltAgent/awesome-agent-skills) (1000+, NarraNexus-compatible SKILL.md)
