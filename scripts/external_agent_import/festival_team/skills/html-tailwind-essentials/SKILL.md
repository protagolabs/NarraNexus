---
name: html-tailwind-essentials
description: How to build the festival website — semantic HTML, mobile-first Tailwind CSS via CDN, single static `index.html`, accessible markup, predictable file layout under `./agent_workspace`. Use whenever writing or editing site files. One small interactive JS component is allowed (a zone filter, accordion, or carousel) — keep dependencies to zero.
---

# Static-Site Build Essentials — HTML + Tailwind

## Stack constraints (for 1-hour POC)

- **Single static site**, no build step, no Next.js / React for this template.
- **Tailwind CSS via CDN** (no `npm install`): `<script src="https://cdn.tailwindcss.com"></script>`.
- **Vanilla JS** for at most ONE small interactive component (filter, accordion, carousel).
- **No external font/CDN that needs API keys**. Google Fonts CDN is fine.
- **Mobile-first**: design for 375px width first; scale up to 768 (tablet) and 1280 (desktop).

## File layout (write to `./agent_workspace/`)

```
agent_workspace/
├── index.html              ← the page (single file)
├── assets/                 ← Visual agent saves images here
│   ├── hero.jpg            ← 1920×1080 hero
│   ├── zone-science.jpg
│   ├── zone-arts.jpg
│   ├── og-share.jpg        ← 1200×630 social card
│   └── icons/              ← inline SVG preferred, raster fallback here
├── style.css               ← optional, only for what Tailwind can't do
└── script.js               ← optional, the ONE interactive component
```

## Semantic HTML skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Great Exhibition Road Festival 2026 — 6–7 June, South Kensington</title>
  <meta name="description" content="<filled by Content agent>" />

  <!-- OG / social -->
  <meta property="og:title" content="..." />
  <meta property="og:description" content="..." />
  <meta property="og:image" content="./assets/og-share.jpg" />
  <meta property="og:type" content="website" />

  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-white text-slate-900 antialiased">
  <header class="..."><!-- nav / logo --></header>
  <main>
    <section class="hero ..."><!-- hero --></section>
    <section class="zones ..."><!-- the museums / themes --></section>
    <section class="schedule ..."><!-- 2-day at-a-glance --></section>
    <section class="getting-there ..."><!-- transport, accessibility --></section>
  </main>
  <footer class="..."><!-- partners + CTA --></footer>
</body>
</html>
```

## Tailwind quick palette (Imperial-leaning)

- Navy: `bg-[#003e74]` / `text-[#003e74]` (Imperial primary)
- Cool grey: `bg-slate-100`, `text-slate-700`
- Accent red (sparingly, for CTAs): `bg-[#dd2515]`
- Off-white background: `bg-stone-50`

Headings: `font-serif` (Tailwind default serif looks museum-appropriate) for hero, `font-sans` for body.

## Accessibility baselines

- Every `<img>` has alt text (Content agent supplies; never empty unless purely decorative — then `alt=""`).
- Heading order is strict: one `<h1>`, then `<h2>`s, never skip levels.
- Color contrast: body text 4.5:1 minimum.
- Focus styles visible: don't strip Tailwind's defaults.
- Touch targets ≥ 44×44px on mobile.

## What to send to QA when done

- Path to the file (`./agent_workspace/index.html`)
- "Ready for review" + the breakpoint list to screenshot (375 / 768 / 1280)
- A short summary of what was implemented + any open decisions for the user
