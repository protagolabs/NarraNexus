# MindFlow · Netmind.AI Auto Coding Documentation System

> Version: 1.0
> A three-tier documentation system designed so that any human or AI agent can
> on-board a codebase and start productive work without oral tribal knowledge.

---

## 1. Why this system exists

Three problems this solves:

1. **Docs rot when they mirror code.** Any doc that repeats function
   signatures, class listings, or "what the code does" will drift out of sync
   within weeks. No amount of CI saves it.
2. **On-boarding a multi-stack project is slow.** A new contributor faces a
   forest of files with no clear reading path from macro architecture to micro
   behavior.
3. **LLM agents lack the context code can't express.** Agents can read code;
   they can't read intent — why a file exists, what was tried before, which
   behaviors look like bugs but are intentional.

The system addresses each by assigning distinct, non-overlapping roles to
three tiers of documentation.

---

## 2. The three tiers

```
Tier-1 · In-code comments
  └── Inline comments, docstrings, file headers
  └── Answers: "What does this line/function do? What are the parameters?"

Tier-2 · .mindflow/mirror/ · Intent mirror
  └── One md per code file, directory structure mirrors source tree
  └── Answers: "Why does this file exist? Who calls it? What decisions shaped it? Where are the traps?"
  └── Never repeats signatures, class lists, or step-by-step behavior

Tier-3 · .mindflow/project/ · References + playbooks
  └── references/ — deep authoritative docs on cross-cutting topics
  └── playbooks/  — task-oriented SOPs with explicit "when to read" triggers
  └── Referenced by CLAUDE.md / AGENTS.md / GEMINI.md with read triggers
```

**Hard rule:** Each tier only contains what the tier below cannot express. No
duplication, no reproof, no competing sources of truth.

---

## 3. Tier-1 · In-code comments

See the project's `coding_standards.md` reference. Tier-1 captures "what and
how"; tier-2 captures "why".

---

## 4. Tier-2 · `mirror/` intent mirror

### 4.1 Coverage rules

Mirror the source code directory tree 1:1 under `.mindflow/mirror/`. Rename
each source file's extension to `.md`. Each directory gets an additional
`_overview.md` file describing the directory's role.

**Include** (each project configures its own list): the source dirs that
matter for intent. For a polyglot project, list each language root separately.

**Exclude**:
- Build artifacts (`__pycache__`, `node_modules`, `target`, `dist`, `.venv`)
- Tests (tracked elsewhere)
- Pure type-definition dirs (covered by one directory-level overview)
- Static assets (`.css`, `.json`, images)
- Empty or pure re-export `__init__.py` (judged by: only `import`,
  `from ... import`, `__all__ = [...]` statements after stripping docstrings)
- Inside private impl directories matching `_*_impl/`: individual files are
  exempt, only `_overview.md` is required (but individually-authored file mds
  are permitted as exceptions for exceptionally important private files)

### 4.2 Content model: intent-only

**Write** (the things code cannot express):
- **Why this file exists** — problem domain, what gap it fills, why it isn't
  merged into another file
- **Cross-file relationships** — who calls me, who I call, shared invariants
  (prose, not signature lists)
- **Design decisions and rejected alternatives** — why it's built this way,
  what was tried, what was rejected and why
- **Known gotchas and edge cases** — historical traps, counter-intuitive
  behaviors
- **New-contributor pitfalls** — things that look wrong but are intentional

**Never write**:
- Function signatures, parameter lists, return types (read the code)
- Class and method listings (read the code)
- "What this function does" (that's tier-1 docstring territory)

### 4.3 Single-file md template

```markdown
---
code_file: <repo-relative path>
last_verified: YYYY-MM-DD
stub: false
---

# <filename> — <one-line role>

## Why this exists
<1-3 paragraphs: problem, scope, reason it's its own file>

## Upstream / downstream
- **Callers**: <prose>
- **Callees / deps**: <prose>

## Design decisions
- <decision 1 + what was rejected and why>

## Gotchas / edge cases
- <trap 1>

## New-contributor pitfalls
- <counter-intuitive point>
```

### 4.4 `_overview.md` template

```markdown
---
code_dir: <repo-relative path ending in />
last_verified: YYYY-MM-DD
---

# <dir>/ — <one-line role>

## Directory role
<what this directory does in the whole system>

## Key file index
- `file.py` — one-line description

## External collaborators
<which other directories this interacts with, in prose>
```

### 4.5 Frontmatter

| Field | Type | Required on | Meaning |
|-------|------|-------------|---------|
| `code_file` | repo-relative path | single-file mds | Points to the source file this md documents |
| `code_dir` | repo-relative path ending in `/` | `_overview.md` | Points to the source directory |
| `last_verified` | ISO date | all | Last day a human confirmed intent is still accurate |
| `stub` | bool | optional | `true` if autogenerated placeholder, `false` when filled |

---

## 5. Tier-3 · `project/` references + playbooks

### 5.1 Two subdirs, two roles

- **`references/`** — authoritative deep-dive docs on subsystems and
  cross-cutting topics (architecture, subsystem X, coding standards, DB
  schema). Stable. Read on demand.
- **`playbooks/`** — task-oriented SOPs with explicit "when to read"
  triggers. Every playbook starts with a trigger ("user asks to add a new
  module") and ends with a completion checklist.

### 5.2 References template

```markdown
---
doc_type: reference
last_verified: YYYY-MM-DD
scope:
  - <source dir this covers>
related_playbooks:
  - ../playbooks/<name>.md
---

# <Topic> — Reference

## What this is
<1-2 paragraphs: scope and explicit non-scope>

## Core concepts
<terms, data structures, roles>

## Structure and flow
<how the system is organized, how data flows; prose + ASCII diagrams OK>

## Invariants / contracts
<rules that must hold; what callers must guarantee; what implementers must preserve>

## Known traps
<historical pitfalls>

## Related code
- `<source path>` → `../../mirror/<source path>.md`

## Related playbooks
- [playbook name](../playbooks/<name>.md)
```

### 5.3 Playbooks template

```markdown
---
doc_type: playbook
last_verified: YYYY-MM-DD
trigger: <one-line precise trigger condition>
prerequisites:
  - <must have read reference X>
related_references:
  - ../references/<name>.md
---

# <Task> — Playbook

## When to follow this
<precise trigger; mirrors the "when to read" line in CLAUDE.md's index>

## Prerequisites
<what must be true before starting>

## Steps

### 1. <Step title>
- **Do**: <action>
- **Files touched**:
  - `path/to/file` — why
- **Acceptance**: <how to know this step worked>

### 2. ...

## Completion checklist
- [ ] Code changes done
- [ ] Corresponding tier-2 mirror mds updated
- [ ] Related references updated if needed
- [ ] Lint / typecheck / tests pass

## Common mistakes
<historical traps for this task>
```

### 5.4 CLAUDE.md integration

CLAUDE.md (or AGENTS.md / GEMINI.md) gains four things:

1. A **Tier-2 sync rule** in the ironclad rules section: whenever a code
   file is modified behaviorally, re-read the corresponding mirror md and
   update intent if the change invalidates it; refresh `last_verified`.
2. A **three-tier doc system** section (~1 paragraph) pointing to
   `.mindflow/README.md`.
3. A **workflow startup** section listing the steps an agent MUST do before
   brainstorming / coding: scan the doc index, read matching playbooks/
   references first, read mirror mds for files about to be edited.
4. A **deep doc index** section listing every reference and playbook with a
   one-line description and — crucially — a **"when to read"** trigger line
   for each. Triggers must be concrete ("when modifying X", "when user asks
   for Y") so agents actually follow them.

Without the workflow-startup section, the index becomes a dead file.

---

## 6. Maintenance: three layers of defense

### 6.1 Layer 1 · Structural invariants (pre-commit hook)

A `check` script validates that:
- Every include-rule code file has a mirror md
- Every mirror md maps back to an existing code file (no orphans)
- Every included directory has an `_overview.md`
- Every frontmatter `code_file` / `code_dir` path exists

Exits non-zero on violation. Installed as a git pre-commit hook and run in CI.

### 6.2 Layer 2 · Content discipline (ironclad rule in CLAUDE.md)

The "tier-2 sync rule" in CLAUDE.md instructs agents/humans to update
corresponding mirror md whenever they make behavioral changes to a code file.
Because the content model is intent-only, most commits do NOT trigger mirror
updates — keeping maintenance cost low.

### 6.3 Layer 3 · Soft staleness detection (`make doc-audit`)

An `audit` script reads each mirror md's `last_verified` and compares against
`git log` for the corresponding code file. Reports a to-do list of mds where
the code has been touched since last verification. Non-blocking — just a
backlog for periodic manual review.

---

## 7. Initial seeding: three phases

### Phase 1 · Batch scaffold

Run a scaffold script that walks the source trees and creates stub mds for
every in-scope file and directory. Stubs use the templates from §4.3 / §4.4
with all content sections filled with `<!-- TODO: intent -->` placeholders.
Frontmatter `stub: true`. Idempotent — never overwrites existing files.

Goal: Layer 1 structural invariants hold from day one, so commits are never
blocked for "missing md" just because scaffolding hasn't caught up.

### Phase 2 · Hand-write critical files

Define a project-specific list of "critical files" — the ~20-40 files whose
intent is worth the most to new contributors. Examples:
- Core runtime entry points
- Base classes of plugin systems
- Non-obvious adapters and facades
- Top-level `_overview.md` for each major directory

Write these by hand in a focused session. These become the on-boarding
starting points.

### Phase 3 · Lazy fill

All other stubs get filled the first time a human or agent touches the
corresponding code file — enforced by the Tier-2 sync rule in CLAUDE.md.
Typical coverage target: 80%+ within 6 months of normal development.

---

## 8. Migrating this system to a new project

1. Copy this `README.md` to the new project's `.mindflow/README.md`.
2. Create the skeleton: `.mindflow/{mirror,project/{references,playbooks}}/`.
3. Copy and adapt `scripts/{scaffold,check,audit}_mindflow.py` and
   `scripts/mindflow_lib.py`. Edit the `INCLUDE_SPECS`, `EXCLUDED_*`, and
   `OVERVIEW_ONLY_*` constants at the top of the library for the new project's
   source layout.
4. Copy `scripts/install_git_hooks.sh` and run it.
5. Add Makefile (or equivalent) targets: `check-mindflow`, `audit-mindflow`,
   `scaffold-mindflow`.
6. Update the project's CLAUDE.md (or AGENTS.md / GEMINI.md) with:
   - Tier-2 sync ironclad rule
   - Three-tier doc system section
   - Workflow startup section
   - Deep doc index section with "when to read" triggers
7. Run `python -m scripts.scaffold_mindflow` to seed stubs.
8. Define and hand-write the project-specific critical file list.
9. Write the first batch of references and playbooks.

---

## 9. Future: packaging as a superpowers skill

This document is written in a structure that converts directly to a
superpowers skill. To package:

1. Create the skill directory.
2. Prepend skill frontmatter:
   ```
   ---
   name: mindflow
   description: Use when setting up or maintaining a three-tier documentation system in a codebase that needs AI + human onboarding support
   ---
   ```
3. The body of this README becomes the skill body largely unchanged.
4. Distribute `scripts/mindflow_lib.py` and the three scripts as skill
   resources.
5. The §8 migration checklist becomes the skill's "how to apply" section.
