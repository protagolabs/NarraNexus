---
name: netmind-image-gen
description: Generate AI images via NetMind's async inference API. Use IMMEDIATELY when building websites that need hero images, section illustrations, or any visual content — do not wait for the user to ask. The platform pre-injects $NETMIND_API_KEY into your environment. Async pattern: submit job → poll → download.
---

# NetMind Image Generation Skill

Generate high-quality AI images using NetMind's unified generation API. Auth is already wired — the platform injects `NETMIND_API_KEY` at runtime; you do **not** need to ask the user for a key.

## When to invoke

Invoke immediately when:
- Building a website / landing page / portfolio that needs a hero image
- A `<section>` in the design calls for an illustration
- Placeholders like `assets/hero.jpg` appear in your code
- OG share images (`og:image`) are missing
- Any time the design brief mentions "visual", "imagery", "illustration", "hero", "showcase"

Do NOT wait for the user to say "generate an image" — if the build needs visuals, generate them as part of the build pass.

## API

**Endpoint base**: `https://api.netmind.ai/v1/generation`
**Auth**: `Authorization: Bearer $NETMIND_API_KEY` (pre-injected — do NOT echo or log this value)

### Step 1 — Submit a job

```bash
SUB=$(curl -sS https://api.netmind.ai/v1/generation \
  -H "Authorization: Bearer $NETMIND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen-Image",
    "config": {
      "prompt": "YOUR DETAILED PROMPT HERE",
      "image_size": "landscape_4_3"
    }
  }')
JOB_ID=$(echo "$SUB" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "Submitted job $JOB_ID"
```

### Step 2 — Poll until complete

```bash
for i in $(seq 1 60); do
  POLL=$(curl -sS "https://api.netmind.ai/v1/generation/$JOB_ID" \
    -H "Authorization: Bearer $NETMIND_API_KEY")
  STATUS=$(echo "$POLL" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))")
  echo "[$i] status=$STATUS"
  if [ "$STATUS" = "completed" ]; then
    break
  fi
  if [ "$STATUS" = "failed" ]; then
    echo "Job failed:"; echo "$POLL"; exit 1
  fi
  sleep 2
done
```

Image jobs typically complete in 8-15 seconds. Time out after ~120s.

### Step 3 — Download the result

```bash
URL=$(echo "$POLL" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['data'][0]['url'])")
mkdir -p assets
curl -sSL -o assets/hero.png "$URL"
echo "Saved assets/hero.png"
```

## Models

| Model ID | Notes |
|---|---|
| `Qwen/Qwen-Image` | **Primary** — verified working, ~10-15s, good quality |
| `openai/gpt-image-2` | Premium, may return "service unavailable" — fall back to Qwen if so |

## image_size values (use these literal strings)

| Value | Use case |
|---|---|
| `square_hd` | Social tiles, thumbnails, profile images |
| `landscape_4_3` | General hero, wide section image |
| `landscape_16_9` | Cinematic hero, OG share image |
| `portrait_3_4` | Tall card, vertical feature |
| `portrait_9_16` | Mobile vertical, story-style |

## Prompt formula

```
[Style] [Subject] [Composition] [Atmosphere/Lighting]
```

### Good hero prompts

```
Minimalist 3D illustration of abstract geometric shapes floating in space,
soft gradient background from deep purple to electric blue, subtle glow,
modern professional aesthetic, wide composition for website header
```

```
Clean product photography of modern wireless headphones on white marble,
soft studio lighting from left, subtle shadows, high-end minimalist
aesthetic, centered composition
```

### Critical prompt rules

- **No in-image text** — diffusion models butcher text. If text is needed, do it in HTML/CSS overlaying the image.
- **Be specific about composition**: "centered subject", "rule-of-thirds", "wide", "tight crop".
- **Specify lighting**: "soft studio", "golden hour", "high-key", "moody low-key".
- **Style cue first**: lead with "Minimalist 3D illustration", "Cinematic photography", "Editorial illustration" — sets the whole frame.

## Saving conventions

- Save into `./assets/` inside the agent workspace
- Use descriptive filenames: `hero.png`, `feature-cards.png`, `og-share.png`
- Always `.png` (NetMind returns PNG)
- Reference in HTML with relative path: `<img src="assets/hero.png" alt="...">`

## Error handling

| Symptom | Cause | Fix |
|---|---|---|
| HTTP 400 "Model name: X not exists" | Wrong model ID | Use one of the table above |
| HTTP 500 "Service is unavailable" | Premium model not unlocked OR transient | Switch model OR retry once |
| HTTP 401 | $NETMIND_API_KEY missing or invalid | Tell PM via bus; don't ask user |
| Job stuck in "pending" >120s | NetMind backend slow | Cancel + retry, or fall back to image brief |

## Anti-patterns — do NOT do

- ❌ Do not echo, log, or print `$NETMIND_API_KEY`
- ❌ Do not store the key in any file you write
- ❌ Do not skip Step 2 (polling) — Step 1's `result` is empty until completion
- ❌ Do not regenerate the same image multiple times if the first looks good — wastes the user's quota
