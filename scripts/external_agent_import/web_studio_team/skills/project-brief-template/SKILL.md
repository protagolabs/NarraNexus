---
name: project-brief-template
description: How the Project Manager captures the user's website brief in 60 seconds on the first turn and persists it as `project_brief.md` in the shared workspace. After this skill runs, every teammate reads `project_brief.md` as the canonical source-of-truth for the project — no agent re-asks the user, no agent invents facts.
---

# Project Brief — first-turn capture + persist

## When to use this

PM ONLY, on the **first user message** of a new website project. Before dispatching any teammate. The output of this skill is the file `./project_brief.md` in the team's shared workspace; every teammate reads it.

## What goes in `project_brief.md`

Capture the following 8 fields. If the user didn't say, pick a SENSIBLE DEFAULT and mark it `(default)` — don't pepper them with questions.

```markdown
# Project Brief

## 1. Project name
<one short noun phrase — e.g. "Acme Conference 2026 site">

## 2. Goal (one sentence)
<why this site exists — who's the audience and what action do you want them to take>

## 3. Audience
<who visits this site — describe in one line>

## 4. Pages / sections (5–7 max)
- <Hero>
- <About>
- <...>

## 5. Tone / brand
- Voice: <e.g. "warm, plain-English, no jargon">
- Palette: <hex codes if user supplied, else "neutral with one accent color (default)">
- Vibe: <e.g. "editorial documentary", "playful", "premium minimalist">

## 6. Tech baseline (default if user didn't specify)
- Single static `index.html` in `./agent_workspace/`
- Tailwind CSS via CDN, no build step (default)
- Mobile-first, 3 breakpoints (375 / 768 / 1280) (default)
- One small interactive JS component (optional)

## 7. Specific must-haves / nice-to-haves
<bullets — features the user explicitly asked for>

## 8. Out-of-scope (be explicit — prevents scope creep)
- <e.g. "no backend, no signup, no payments">
- <e.g. "no React, no build step">
```

## How the PM uses this

1. **Read the user's first message.**
2. **Restate in 3-5 lines** back to the user — this is your "did I hear you right?" sanity check.
3. **Write `./project_brief.md`** to the team's shared workspace using `Write`.
4. **Post the brief to the team room** via `bus_send_message(channel_id=..., content=...)` — paste the brief content so teammates can see it AND know `project_brief.md` is now canonical.
5. **Dispatch teammates** with @-mentions in the same message or as a follow-up.
6. **Do NOT ask clarifying questions** unless something is genuinely ambiguous in a blocking way. Use defaults aggressively.

## How teammates use this

Every other agent (Designer, Content, Visual, QA), on **first activation**:

1. **Read `./project_brief.md`** with `Read`.
2. **Treat it as canonical**. Don't ask the PM "what's the project" — the answer is in the file.
3. **Only escalate** to PM via `bus_send_message` if a field is genuinely missing or contradictory.

## Example

User: *"Build a marketing site for our AI tool launch next month — make it feel premium."*

PM produces:

```markdown
# Project Brief

## 1. Project name
AI Tool Launch — marketing site

## 2. Goal
Drive signups for the AI tool launching next month.

## 3. Audience
Technical founders + product managers evaluating AI tools.

## 4. Pages / sections
- Hero (with launch date + CTA)
- The Problem
- The Solution (product demo)
- Pricing teaser
- FAQ
- Footer / CTA

## 5. Tone / brand
- Voice: confident, technical, no marketing-speak
- Palette: deep navy + 1 accent (default — refine with Designer if asked)
- Vibe: premium minimalist

## 6. Tech baseline (defaults applied)
- Single static `index.html`, Tailwind via CDN, mobile-first, 3 breakpoints
- One interactive: hero scroll-fade

## 7. Must-haves
- Launch date visible above the fold
- Email capture form (form posts to placeholder; user wires later)

## 8. Out-of-scope
- No actual backend / no real form submission
- No animations beyond the scroll-fade
```
