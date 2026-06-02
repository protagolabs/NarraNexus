# Status · Template Pipeline POC — Wrap-up

**Date**: 2026-05-28 (start) → 2026-05-29 (wave 2) → **2026-06-02 (this wrap-up)**
**Status**: POC **complete**, pipeline proven end-to-end, 4 production templates live on website dev
**Branch**: `feat/external-agent-import` (NarraNexus) · `dev` (narranexus-website)

---

## What we built

A reusable pipeline that converts publicly-available agent configurations into NarraNexus `.nxbundle` templates, proven across **two independent source formats**.

### Scripts (all in `scripts/external_agent_import/`, stdlib-only)

| Script | Purpose |
|---|---|
| `nxbundle_lib.py` | Shared primitives: rebrand pass, module-instance stamps, workspace.tar.gz packing, bundle ZIP assembly |
| `convert_single.py` | One SOUL.md → single-agent `.nxbundle`, optional `--skill-dir` to bundle skills |
| `convert_team.py` | Team spec JSON → multi-agent `.nxbundle`, populates `manifest.team` |
| `auto_team_detect.py` | Scan SOUL.md cross-refs, score cluster candidates |
| `convert_crewai.py` | CrewAI `agents.yaml + tasks.yaml` → multi-agent `.nxbundle` |

### Bundles produced (11 total)

**OpenClaw source** (`mergisi/awesome-openclaw-agents`, MIT, 199 SOUL.md / 24 categories)

| Bundle | Category | Skills | Notes |
|---|---|---|---|
| orion | productivity | 0 | First POC, cloud-import-verified |
| lens (code reviewer) | development | 3 | git-commit-writer + excalidraw-architecture + cost-optimizer |
| github-pr-reviewer | development | 1 | git-commit-writer |
| coordinator_trio (team) | productivity + marketing + business | 3 | **Auto-detected** team (Orion → Echo, Radar cross-refs) |
| overnight-coder | automation | 2 | git-commit-writer + cost-optimizer |
| sql-assistant | data | 1 | model-cost-compare |
| morning-briefing | automation | 0 | Pure awareness (skipped from website; NN already had one) |
| travel-planner | personal | 0 | Pure awareness |
| phishing-detector | security | 0 | Pure awareness |

**CrewAI source** (`crewAIInc/crewAI-examples/crews`, MIT, 16 example crews)

| Bundle | Agents | Notes |
|---|---|---|
| crewai_marketing_strategy | 4 | Lead Market Analyst / Chief Marketing Strategist / Creative Content Creator / Chief Creative Director |
| crewai_recruitment | 4 | Researcher / Matcher / Communicator / Reporter |

### Live on narranexus-website (`dev`, deploying to website.narra.nexus)

**4 of the OpenClaw bundles are now real templates with full metadata**:
- `/templates/overnight-coder`
- `/templates/sql-assistant`
- `/templates/travel-planner`
- `/templates/phishing-detector`

Each has hand-written rich `short_description` + `long_description` + `usage_tip` plus computed `bundle_sha256` and `bundle_size_bytes`. Author attribution: **OpenClaw community (MIT)** with link back to source. See `lib/templates.ts` on `narranexus-website` `dev`.

---

## Pipeline architecture (proven)

```
ANY SOURCE FORMAT
  │
  ├─► OpenClaw SOUL.md ─┐
  ├─► CrewAI YAML ─────┤
  ├─► (future) Letta .af, Claude Code subagent, etc.
  │                     │
  ▼                     ▼
parse                build_agent_files()  ←─ shared primitive
  │                  - rebrand (OpenClaw → NarraNexus)
  │                  - awareness.json composition
  │                  - module-instance stamps × 5
  │                  - workspace.tar.gz with skills
  ▼                     │
                        ▼
                    write_bundle()  ←─ shared primitive
                        │
                        ▼
                    .nxbundle (ZIP with manifest + agents + bus.json …)
```

**Key insight**: the abstraction is correct — adding a new source (`convert_letta.py` etc.) only needs the source-specific parser; the bundle assembly path is unchanged.

---

## Source-attribution + safety

Every produced bundle carries `manifest.source_attribution`:
```json
{
  "agent_id": "...",
  "source": { "repo": "github:mergisi/awesome-openclaw-agents",
              "path": "agents/<cat>/<id>/SOUL.md",
              "license": "MIT" },
  "rebrand_diffs": ["powered by OpenClaw -> powered by NarraNexus", ...]
}
```
OpenClaw platform refs rebranded; peer-agent name refs (Echo, Radar) preserved so detected teams stay coherent on import.

---

## Status of the original plan (PRD §6 alternatives)

| Path | Status |
|---|---|
| A. Batch convert OpenClaw SOUL.md | ✅ **Pipeline done, 9 bundles produced. Ready to scale to remaining 190.** |
| B. Runtime "Import from URL" | 📋 Not started — depends on A landing |
| C. LLM-rebrand Stage B (head-of-pack polish) | 📋 Hook stubbed in `nxbundle_lib.rebrand()`; not wired to live LLM |
| D. Universal intermediate format | ❌ Not needed yet — direct adapters are working |
| E. Other sources (Letta `.af`, Cursor rules, GPTs) | 📋 Pipeline is ready (just write `convert_<source>.py`) |
| F. Claude Code subagents (original v0.1 mainline) | 📋 Demoted to fallback; not started |

**Bucket B was explicitly parked**: searched for "OpenClaw-like single-repo team" projects (MetaGPT / ChatDev / etc.) — they're 2023-era + Python-hardcoded teams, not "config-as-data". No good 2026 replacements found. Held back without blocking.

---

## What's "wrapped up" / what's still parked

**Wrapped up (POC phase complete)**:
- ✅ Pipeline design + 5 scripts
- ✅ 11 bundles produced
- ✅ End-to-end verified (Orion cloud import)
- ✅ Two independent sources working (OpenClaw + CrewAI)
- ✅ 4 production templates on website dev
- ✅ CI/CD fix for divergent EC2 branches (`git reset --hard origin` instead of `git pull`)
- ✅ Source attribution + license handling

**Parked for future sessions**:
- ▶ Scale OpenClaw conversion to remaining 190 agents
- ▶ Convert remaining 14 CrewAI crews
- ▶ Final goal — **skill-pool reverse-generation** (use VoltAgent 1000+ skills as the basis, LLM weaves identity around skill bundles to produce original-but-batched templates, not dependent on external agent libs)
- ▶ LLM rebrand Stage B (live Anthropic SDK call, cache by hash, quality gate)
- ▶ Runtime "Import from URL" UX in the website

---

## Pointers

- PRD: `reference/self_notebook/specs/2026-05-22-batch-template-pipeline-design.md` (gitignored, private notebook)
- Session log: `drafts/logs/batch_template_pipeline_2026_05_22.md`
- Scripts: `scripts/external_agent_import/` (5 scripts + 11 bundles + examples)
- README: [`README.md`](./README.md)
- Branch: `feat/external-agent-import` (4 commits ahead of main, on `origin`)
- Website template entries: `narranexus-website` `dev` `lib/templates.ts` (+4 entries)
