# README_advice.md

> A list of suggested edits to `README.md` collected during the
> repo-governance work in branch `chore/repo-governance`. Each entry
> states **where**, **what**, and **why**. This is for the teammate
> who's rewriting the README — please use, ignore, or amend at your
> discretion.
>
> Delete this file once the README changes have been merged. It's
> intentionally transient.

---

## #1. Add an "AI-assisted contributing" pointer near the top

**Where**: between the current `## What Makes NarraNexus Different`
section (ends around line 50) and `## Quick Start` (starts around
line 52). Or anywhere in the first screenful — the point is that a
first-time visitor sees it before scrolling.

**What** — suggested copy, please edit to match the README voice:

```markdown
## Contributing (AI-assisted by design)

This codebase is built to work well with AI coding assistants. Two
files give your AI complete project context out of the box:

- [`CLAUDE.md`](./CLAUDE.md) — the project's binding rules
- [`.mindflow/_overview.md`](./.mindflow/_overview.md) — index into our
  three-tier doc system (mirror docs + playbooks + references)

Drop these into Claude Code / Cursor / Continue / etc. and your AI
will follow our conventions without you having to read all of them.
See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the full picture.
```

**Why**: This is the differentiator vs other agent frameworks.
Burying it inside `CONTRIBUTING.md` means visitors never see it.
First screenful gets the most attention.

**Length**: 6–10 lines. Don't expand into a tutorial — `CONTRIBUTING.md`
already handles that.

---

## #2. Update the "Install from Source" version reference

**Where**: line ~74, the `git clone` URL.

**What**: currently the clone URL points at
`https://github.com/NetMindAI-Open/NarraNexus.git`. The primary
development repo is `https://github.com/protagolabs/NarraNexus.git`
(see `git remote -v`). Suggest changing to the primary, or showing
both with a one-line note.

**Why**: Mismatched clone URL means new contributors fork the wrong
repo and their PR can't be auto-merged back. Came up during the
repo-governance audit.

---

## #3. Add a governance / contributing section (link out, don't expand)

**Where**: between `## Acknowledgments` (line ~190) and `## Citation`
(line ~198), or right above `## License`.

**What** — suggested copy:

```markdown
## Contributing & governance

- New contributors: start with [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- AI editors: read [`AGENTS.md`](./AGENTS.md) (vendor-neutral) or
  [`CLAUDE.md`](./CLAUDE.md) directly.
- How the project is run, how to become a maintainer:
  [`GOVERNANCE.md`](./GOVERNANCE.md), [`MAINTAINERS.md`](./MAINTAINERS.md).
- Community standards: [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).
- Security policy: [`SECURITY.md`](./SECURITY.md).
```

**Why**: GitHub auto-detects these files but doesn't link them from
the README. A single navigation block lets new visitors find the
whole governance surface in one place.

**Length**: keep it under 10 lines. It's a directory, not a tour.

---

## #4. (Optional) Add a contributor recognition note

**Where**: inside the existing `## Acknowledgments` block (line ~190).

**What**: after the existing acknowledgments paragraph, add a one-line
pointer:

```markdown
See [`MAINTAINERS.md`](./MAINTAINERS.md) for the current maintainer
team. Run `git shortlog -sn` for the full contributor list.
```

**Why**: explicit recognition is cheap and makes new contributors
feel that their commits will be visible. `.mailmap` now consolidates
identities so `git shortlog -sn` produces a clean roll.

---

## #5. (Optional) Move install dependency table earlier OR keep where it is — your call

**Where**: line ~68, the Prerequisites table.

**What**: if you're already restructuring Quick Start, consider
collapsing macOS / Linux / Windows-WSL install hints into a single
"`uv` and Node 20 — see below" line, and putting an expandable
`<details>` block underneath for the per-OS commands. Optional —
purely a length-of-screen-real-estate question.

**Why**: no blocking reason. Just an observation that the
Prerequisites table is one of the heaviest things above the fold and
might warrant a fold.

---

## #6. Add a "Report a bug / Get help" link above the fold

**Where**: as a one-liner near the badge row (around line 18, near
the EN / 中文 link), or right under the tagline.

**What** — suggested copy:

```markdown
**Found a bug or need help?** [Open an issue](https://github.com/protagolabs/NarraNexus/issues/new/choose)
or [start a discussion](https://github.com/protagolabs/NarraNexus/discussions).
```

**Why**: The cold-read found that a non-developer who lands on the
repo after a crash has to scroll past the marketing copy and the
Quick Start before any path to "report this problem" is visible.
One sentence above the fold solves this without polluting the README.

---

## #7. Add a short "Troubleshooting / where logs live" section

**Where**: between `## Quick Start` (line ~52) and
`## LLM Provider Configuration` (line ~103), or just before the
Acknowledgments block.

**What** — suggested copy:

```markdown
## Troubleshooting

If something breaks, the most useful thing to attach to a bug report is logs:

| Install                | Log location                                                   |
| ---------------------- | --------------------------------------------------------------- |
| `bash run.sh`          | `~/.narranexus/logs/backend/`                                  |
| `make dev-backend` etc.| `~/.narranexus/logs/backend/`                                  |
| Self-hosted Docker     | `docker logs narranexus-backend` (also `-poller`, `-mcp`, etc.)|
| Desktop DMG (macOS)    | `~/Library/Logs/com.narranexus.app/` + Console.app User Reports |
| Cloud                  | Include a request id + timestamp in your issue; we'll fetch    |

See [`SECURITY.md`](./SECURITY.md) before sharing logs publicly — redact API keys.
```

**Why**: The bug template asks for logs but a user who hasn't opened
the template yet doesn't know where to look. Surfacing log paths in
the README means users find logs **before** they open an issue, not
mid-form.

---

## Things I deliberately did NOT add

A few things were considered and rejected during the governance work.
Mentioning them so you don't reinvent the conversation:

- **No marketing badge for "AI-friendly"** in the README banner. Bin哥's
  feedback was that hero badges feel like marketing fluff — a plain
  paragraph carries more credibility.
- **No emoji-laden section headers**. Keep README serious; `CONTRIBUTING.md`
  is where the voice is more conversational.
- **No vendor lock-in** (e.g. no "Powered by Claude" branding). The
  AI-friendliness comes from the doc structure, not any single vendor.

---

## How to verify your changes

After your edits, sanity-check from a new visitor's perspective:

1. Open the README on GitHub. Without scrolling past the fold, can you
   answer: *"What is this and how do I run it?"* — should be yes.
2. From the README alone, can you find your way to:
   - The contributing guide → ✅ if CONTRIBUTING.md is linked at least once
   - AGENTS.md / CLAUDE.md → ✅ via the AI section
   - Security reporting → ✅ via the Contributing & governance block
3. Click every internal link. Make sure no `.md` path 404s after
   your rename / move.

When this file's advice has all been incorporated (or explicitly
rejected), please delete `README_advice.md`. It's clutter once we're
done.
