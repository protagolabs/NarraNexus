---
name: image-gen-mcp
description: How to generate project imagery — hero, zone images, OG share card, icons — via an image-generation MCP server. Use when copy is ready and the Designer needs assets. Save outputs to `./agent_workspace/assets/` with predictable filenames so the Designer can wire them up.
---

# Image Generation — usage guide

## Setup paths (user picks one — see SETUP.md)

### Path A — Gemini / Nano Banana (cheapest, recommended for POC)
- Strong on photorealism + multi-reference consistency + in-image text
- Requires `GEMINI_API_KEY` env var (Google AI Studio free tier covers POCs)
- MCP: `image-gen-mcp` (Gemini-based), several community implementations exist

### Path B — OpenAI gpt-image
- Strongest on **crisp typography / logos / wordmarks**
- Requires `OPENAI_API_KEY`
- Cost: ~$0.005 (low) to ~$0.40 (high-4K) per image

### Path C — fal.ai (Flux Pro / Schnell)
- Best for **brand-consistent batches**; Schnell is the cheap draft mode
- Requires `FAL_KEY`

**Pragmatic workflow**: draft cheaply (Gemini or Flux Schnell) → finalize hero on Nano Banana Pro or gpt-image-2.

## Standard asset set for this template

Save everything under `./agent_workspace/assets/`:

| File | Size | Purpose | Prompt direction |
|---|---|---|---|
| `hero.jpg` | 1920×1080 | Above-the-fold hero | Wide  street scene, families exploring science-meets-arts installations, Exhibition Road's Victorian museum architecture in soft daylight, vibrant but warm tones, no in-image text |
| `zone-science.jpg` | 800×600 | Science zone card | Interactive hands-on experiment, mixed-age audience, museum interior |
| `zone-arts.jpg` | 800×600 | Arts zone card | Performance / installation, V&A or RAH aesthetic, dramatic lighting |
| `zone-family.jpg` | 800×600 | Family/workshops card | Kids doing a creative workshop, science museum vibe |
| `og-share.jpg` | 1200×630 | Social share card | Combined identity image with `the project · <dates from project_brief.md>` text — use gpt-image for the text-on-image quality |
| `icon-date.svg` | inline | Calendar icon | (Use inline SVG, no gen needed) |
| `icon-pin.svg` | inline | Location pin | (Use inline SVG, no gen needed) |

## Prompting rules

- **Style consistency**: append the same style tail to every prompt — "warm daylight, slight film grain, editorial documentary photography, no text".
- **Diversity baked in**: include "mixed ages, mixed ethnicities, families and adults".
- **NO LOGOS or names in-image** for hero/zones — branding lives in HTML, not pixels, not pixels. The ONE exception is the OG share card where text is intended.
- **Aspect ratio** matters — never accept a near-square when 16:9 was asked; regenerate.

## Calling the MCP (after install)

Typical tool name: `generate_image` (varies by server — check via `mcp_list_tools`).
Pass:
```json
{
  "prompt": "Wide  street scene during the the project — families exploring science installations, Victorian museum facades in soft afternoon light, mixed crowd, editorial documentary photography, warm tones, no text in image",
  "aspect_ratio": "16:9",
  "model": "nano-banana-pro",
  "output_path": "./agent_workspace/assets/hero.jpg"
}
```

## Rules

- **One quality pass on the hero**, draft mode for everything else, never both.
- **Don't generate images of identifiable real people** — anonymize / use crowds at distance.
- **Never put generated text into image-pixels** unless the file is `og-share.jpg`.
- **Save with predictable filenames** above — Designer wires them up blindly.
- **Cost budget**: ~5-7 final images for the POC. Don't bulk-generate.
