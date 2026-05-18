# AGENT.md

> Vendor-neutral entry point for AI coding assistants. If your editor
> auto-loads `AGENT.md` (or `AGENTS.md`), this is where to start.
> If your editor only loads `CLAUDE.md`, read that one directly — the
> two files point at the same project knowledge.

## What this project is

NarraNexus is a long-memory, hot-pluggable agent framework. The
codebase is organized so that an AI assistant can pick up complete
project context from two files, no human onboarding needed.

### Three names you'll see — all the same thing

- **NarraNexus** — the project / repo / product name (use this in
  user-facing copy)
- **`xyz_agent_context`** — the Python package name (`import
  xyz_agent_context`). Older naming, kept stable to avoid a breaking
  rename. When you see this in import paths or shell output, it's
  the same project.
- **NexusAgent** — an older internal name still appearing in some
  doc titles inside `.mindflow/`. Treat as a synonym for NarraNexus.

### About `CLAUDE.md`'s language

`CLAUDE.md` is mostly written in Chinese. The numbered "铁律"
(binding rules) section is the most important — every numbered rule
is a hard constraint on your output. Names and code identifiers in
the file are in English; pattern-match those when in doubt. The
project explicitly accepts Chinese in documentation (rule #1 only
requires English for code).

## Your authoritative context (read these first)

1. **[`CLAUDE.md`](./CLAUDE.md)** — the project's binding rules. These
   are non-negotiable: architecture invariants, naming conventions,
   doc-sync requirements (rule #10 is important — when you change a
   `.py / .ts / .tsx / .rs` file, also update its matching
   `.mindflow/mirror/<path>.md`), and what's forbidden. Treat every
   numbered rule in §"铁律" as a hard constraint on your output.

2. **[`.mindflow/_overview.md`](./.mindflow/_overview.md)** — index to
   the three-tier documentation system:
   - **Tier-1** — inline comments / docstrings / file headers
   - **Tier-2** — `.mindflow/mirror/` — one md per source file with
     the **intent** of that file (why it exists, what surprises a
     reader). Read the matching mirror md *before* editing a source
     file.
   - **Tier-3** — `.mindflow/project/`:
     - `playbooks/` — task-shaped SOPs (add a module, add a table,
       debug runtime, …). Match the task to a playbook and follow it.
     - `references/` — deep authoritative docs (architecture, narrative
       system, module system, …).

3. **[`CONTRIBUTING.md`](./CONTRIBUTING.md)** — human-facing flow
   (issues, PR conventions, branch naming, squash-merge policy). Skim
   §0 and §2 once; the rest you can ignore unless asked.

## Working pattern we expect from you

1. **Before editing**: read the matching mirror md
   (`.mindflow/mirror/<path>.md`). It tells you the file's intent,
   upstream/downstream, gotchas.
2. **While editing**: follow the binding rules in `CLAUDE.md`. Code in
   English, comments minimal, no backward-compatibility shims, no
   modules importing from each other.
3. **After editing**: if your change altered behavior of a
   `.py / .ts / .tsx / .rs` file, update the matching mirror md in the
   **same commit** — refresh the `last_verified` frontmatter, rewrite
   the intent paragraph if needed. This is binding rule #10.
4. **New source file**: create its mirror md in the same commit.
5. **Deleted source file**: delete its mirror md in the same commit.

## What not to do

- Don't import across modules (`module/a_module/` cannot import from
  `module/b_module/`).
- Don't add backward-compatibility shims, deprecation wrappers, or
  silent fallbacks. The project is young; change cleanly.
- Don't hard-code scenario-specific content (sales, customer support,
  etc.) into general-purpose prompts. Scenarios belong in agent
  Awareness, not the framework.
- Don't write Chinese in source code. Comments, identifiers, strings —
  all English. Documentation can be either language.
- Don't propose hard time/iteration caps on `agent_loop` as a "fix"
  (rule #14 — long agent runs are first-class).

## Common tasks — pointer index

If the user's task matches one of these, jump straight to the linked
playbook / reference instead of re-deriving:

| Task                                          | Where to go                                                                   |
| --------------------------------------------- | ----------------------------------------------------------------------------- |
| Add a new Module (hot-pluggable capability)   | [`.mindflow/project/playbooks/add_new_module.md`](./.mindflow/project/playbooks/add_new_module.md) |
| Add or change a database table                | `CLAUDE.md` §"数据库表注册"; `.mindflow/project/references/database_schema.md` |
| Touch auth / identity / X-User-Id flow        | `backend/auth.py.md` mirror (read it first), then `.mindflow/mirror/backend/routes/auth.py.md` |
| Modify bundle export                          | `.mindflow/mirror/src/xyz_agent_context/bundle/builder.py.md` |
| Add a Tier-2 mirror md for an existing source | `.mindflow/project/playbooks/README.md` "Writing a new playbook"-style guidance applies; mirror the pattern in `.mindflow/mirror/` |
| Debug runtime errors                          | `CLAUDE.md` §"易忘事项"; check the `agent_runtime` mirror docs |

When the playbook for your exact task doesn't exist (the `playbooks/`
directory is still being populated), the fallback is:

1. Read `CLAUDE.md` summary section for the topic
2. Read `.mindflow/project/references/` if it's a deep architecture
   question
3. Read the matching `.mindflow/mirror/<path>.md`
4. Backfill the playbook in your PR as you go — see
   `.mindflow/project/playbooks/README.md`

## When you finish

Hand off a normal PR or commit summary. The human will review,
maintainer reviews go through the standard PR template. If your work
introduced a new file pattern or convention not covered above, mention
it explicitly so it can be folded into `CLAUDE.md`.

---

## Why this file exists alongside `CLAUDE.md`

`CLAUDE.md` is the canonical rule book. `AGENT.md` is a vendor-neutral
alias so editors that look for `AGENT.md` / `AGENTS.md` (Cursor,
Continue, Aider, Cline, and the cross-vendor `agents.md` proposal) land
in the same place. Both files are kept aligned; if they ever diverge,
`CLAUDE.md` wins.
