---
name: copywriting-essentials
description: How to write the website's copy — hero headline, zone blurbs, CTAs, alt-text, SEO meta, OG tags. Use whenever the Content agent is producing or editing prose. Tone is warm, inclusive, accessible to non-scientists; every claim either lives in the project-brief-template or has a web-search citation.
---

# Site Copy — essentials

## Tone of voice

- **Warm & inclusive** — "for everyone, free, all ages"
- **Plain English** — no academic jargon. Reading age ~14.
- **Specific over abstract** — "55,000 visitors in 2025" beats "a popular event".
- **Active voice** — "Experience" / "Explore" / "Meet" beat "Visitors can experience".
- **British English** spelling (project is in London) — "colour", "organise", "programme".

## The page sections + their copy targets

| Section | Element | Length | Key beats |
|---|---|---|---|
| Hero | H1 | ≤ 8 words | Project name + emotional hook |
| Hero | Subtitle | 1 sentence ≤ 20 words | Date + free + audience |
| Hero | CTA button | 2–4 words | "Plan your visit" / "Explore the project" |
| About | H2 + 2–3 sentences | ~60 words | What it is, scale (55k visitors), partners |
| Zones | One blurb per zone | ~25 words each | What you'll experience + which institutions |
| Schedule | At-a-glance card | ≤ 40 words | Sat + Sun general activity types |
| Getting there | List + 1 paragraph | ~80 words |  tube, walkable cluster, accessibility |
| Footer | Partner list + free disclaimer | ≤ 30 words | "Presented by the project owner with [partners]. Free to attend." |

## SEO + social meta

```html
<title>the project 2026 — 6–7 June,  (Free)</title>
<meta name="description"
      content="the project owner's free annual celebration of science and the arts returns <dates from project_brief.md> in  — interactive exhibits, workshops, talks, and performances with the <project entities>, Science Museum, V&A, and Royal Albert Hall.">

<meta property="og:title" content="the project · <dates from project_brief.md>">
<meta property="og:description" content="Free for all ages — interactive science and the arts in London's Exhibition Road. With <project entities>, Science Museum, V&A and Royal Albert Hall.">
<meta property="og:image" content="./assets/og-share.jpg">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
```

## Alt-text rules

- Describe what the image shows for someone who can't see it
- 8–15 words is the sweet spot
- Don't say "image of" or "picture of"
- For decorative images, use empty alt (`alt=""`) and let it pass through
- Examples:
  - Good: "A child looks through a microscope at a Science Museum workshop"
  - Bad: "Image of science"

## CTA writing

- Verb-first, second-person implied: **"Plan your visit"**, **"See the programme"**, **"Find your zone"**
- One primary CTA per section
- Avoid "Click here" and "Learn more"

## What to send to the Designer

When done, hand off as a single markdown block:

````
HERO H1: the project
HERO SUBTITLE: ...
HERO CTA: Plan your visit

ABOUT H2: ...
ABOUT BODY: ...

ZONE-SCIENCE BLURB: ...
ZONE-ARTS BLURB: ...
...

SEO TITLE: ...
SEO DESCRIPTION: ...
OG TITLE: ...
OG DESCRIPTION: ...

ALT TEXTS:
- hero.jpg: ...
- zone-science.jpg: ...
- zone-arts.jpg: ...
- og-share.jpg: ...
````

That way the Designer can paste directly into the template.
