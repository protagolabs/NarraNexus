# Skill Examples Catalog

This directory holds the **curated catalog** of real open-source Claude
Code skills used by the skill benchmark pipeline. We deliberately picked
*practical, real-world task* skills (job search, paper search, social
information, news digest) rather than file-processing or code-generation
skills, because:

1. Their output is **plain text / markdown** — easy to inspect and verify.
2. They cover **typical real-world user intents**.
3. They have **zero or minimal API key / account requirements**.
4. None of them generate images, HTML pages, or executable code as primary
   output, so verification stays content-based.

## Current entries (`skills.yaml`)

| id                 | task                          | API keys | source |
|--------------------|-------------------------------|----------|--------|
| `claude-jobs`      | Tech-company job search       | None     | [jshchnz/claude-jobs](https://github.com/jshchnz/claude-jobs) |
| `paper-search`     | Academic paper search via OpenAlex | None | [ykdojo/paper-search](https://github.com/ykdojo/paper-search) |
| `reddit-fetch`     | Reddit posts and comments via public JSON API | None | [ykdojo/claude-code-tips](https://github.com/ykdojo/claude-code-tips/tree/main/skills/reddit-fetch) |
| `ai-daily-digest`  | RSS-based daily tech news digest (92 blogs) | None | [HarrisHan/ai-daily-digest](https://github.com/HarrisHan/ai-daily-digest) |

Each entry includes:

- `description` — human-readable summary
- `source_url` — upstream GitHub repo
- `install_kind` + `install_hint` — best-guess install path (NOT yet
  finalized; see Open Questions below)
- `install_turns` — natural-language user messages that drive the agent
  to install + study the skill during Phase 1 (Replay)
- `qa_questions` — 5 realistic user questions a person would actually
  ask this skill, drawn from each skill's own README and use cases
- `verify_hints` — what to check in the dump to confirm the skill was
  used (Level 1) and that the output looks correct (Level 2)

## Pipeline

This catalog is consumed by:

```
scripts/run_skill_install_and_qa.py --catalog benchmark/skill_examples/skills.yaml ...
```

which runs the three-phase Replay → QA → Cleanup loop defined in that
script. Cleanup is handled by `scripts/cleanup_skill_state.py`. Module
isolation is configured by `benchmark/test_configs/skill_isolation.yaml`.

## Open questions before first run

1. **Which install method is canonical?** Each skill ships with its own
   instructions:
   - `claude-jobs`: `git clone ... ~/.claude/skills/claude-jobs`
   - `paper-search`: `claude plugin marketplace add ...` + `claude plugin install ...`
   - `reddit-fetch`: subdir of a larger repo — needs sparse-checkout
   - `ai-daily-digest`: `git clone` or `clawhub install`

   The benchmark always wants the skill in
   `{agent_workspace}/{agent_id}_{user_id}/skills/<name>/`, so a plain
   `git clone` into that path is the lowest-common-denominator. We may
   need a small wrapper that:
   - normalizes target dir to the agent workspace
   - handles sparse subdir extraction for `reddit-fetch`

2. **Does the agent actually need to run `git clone`, or do we pre-install?**
   For deterministic benchmarking we may want to pre-install all four skills
   and skip the install dialogue, testing only dispatch + usage. The current
   catalog supports both modes (pipeline can be told to skip install_turns).

3. **HEARTBEAT.md / scheduled jobs?** `ai-daily-digest` could plausibly
   create a scheduled job ("digest daily at 9am") during study. The current
   `skill_isolation.yaml` keeps `JobModule` active so this pathway is
   observable; the cleanup script cancels any jobs created.
