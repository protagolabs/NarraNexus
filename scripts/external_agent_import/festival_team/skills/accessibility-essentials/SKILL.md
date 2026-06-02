---
name: accessibility-essentials
description: How to QA the festival site for accessibility — WCAG 2.1 AA basics, semantic HTML, color contrast, keyboard navigation, alt-text. Use whenever you're reviewing the built site, before declaring it ready. Pair with the playwright-mcp skill to actually inspect the rendered output.
---

# Accessibility Review — essentials

Target: **WCAG 2.1 Level AA** baseline. We're not chasing AAA in a 1-hour POC, but AA is non-negotiable for an Imperial-branded site.

## The 10-item review checklist

Run through each item against the live site at `http://localhost:8000`. Use Playwright MCP for evidence.

| # | Check | What good looks like | How to verify |
|---|---|---|---|
| 1 | **Page title** | `<title>` is specific, mentions festival + date | `browser_snapshot` → title field |
| 2 | **One H1 only** | Exactly one `<h1>`, on the hero | Snapshot heading tree |
| 3 | **Heading order** | h1 → h2 → h3, no skipped levels | Snapshot heading tree |
| 4 | **Image alt text** | Every `<img>` has `alt`. Decorative = empty `alt=""` | Snapshot all `<img>` |
| 5 | **Color contrast** | Body 4.5:1, large text 3:1 | Eyeball + screenshot |
| 6 | **Focus visible** | Tab through links/buttons — outline visible | Click + Tab in Playwright |
| 7 | **Touch targets** | Buttons/links ≥ 44×44px on mobile | Mobile screenshot inspect |
| 8 | **Link text** | Descriptive ("See the programme") not "Click here" | Snapshot link list |
| 9 | **Form labels** | Every input has `<label>` (if any forms) | Snapshot form |
| 10 | **Meta** | OG title + description + image, viewport meta, charset | Snapshot `<head>` |

## Severity ranking (when reporting to Designer)

- **BLOCKER**: missing `<title>`, no `<h1>`, body contrast <4.5:1, broken responsive at 375px, content overflows page
- **HIGH**: heading-order skip, empty alt on content image, missing OG image, no focus ring on interactive elements
- **MEDIUM**: link text not descriptive, touch target slightly under 44px, color near contrast threshold
- **LOW**: prefers `<button>` over `<a role="button">`, missing `prefers-reduced-motion` for transitions

Report format to Designer via @mention:

```
@Web Designer Review complete. Site is BLOCKER-free / has N BLOCKERs.

BLOCKERS (fix before ship):
- [B1] ...

HIGH:
- [H1] ...

Screenshots: ./qa/mobile.png, ./qa/tablet.png, ./qa/desktop.png
```

## Things to stop checking after AA

Don't waste cycles in a 1-hour build on:
- WCAG AAA enhanced contrast (7:1)
- Sign language video alternatives
- Live captions for non-existent video
- AAA-level focus indicators

These are valid goals but not the POC's job.

## Content-vs-copy check (do this too)

The QA role also verifies copy matches the festival-fact-sheet:
- Dates say "6–7 June 2026" ✓
- Free is mentioned in hero or above the fold ✓
- The 4 partner institutions are listed correctly ✓ (Natural History Museum, Science Museum, V&A, Royal Albert Hall)
- No invented named events / speakers / specific times
