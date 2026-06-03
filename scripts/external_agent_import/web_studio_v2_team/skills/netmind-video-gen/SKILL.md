---
name: netmind-video-gen
description: Generate AI videos via NetMind's async API using google/veo3.1-fast. Use when a website explicitly needs a short video hero, looping background video, product demo clip, or similar. The platform pre-injects $NETMIND_API_KEY. Video generation is async and takes longer than images (typically 30-90 seconds).
---

# NetMind Video Generation Skill

Generate short AI videos via NetMind's unified async API (model: `google/veo3.1-fast`). Auth is pre-wired — `NETMIND_API_KEY` is injected by the platform.

## When to invoke

Only invoke when:
- Brief explicitly asks for a video hero / background loop / product demo clip
- A `<video>` element in the design needs source content
- Site is for an event / product launch where motion would meaningfully lift the page

Do NOT use video generation just to "make the site fancier" — videos cost more time and tokens than images. Static + good typography beats a janky video.

## API

Same async pattern as image gen, different model.

**Endpoint base**: `https://api.netmind.ai/v1/generation`
**Auth**: `Authorization: Bearer $NETMIND_API_KEY` (pre-injected — never echo)
**Model**: `google/veo3.1-fast`

### Step 1 — Submit

```bash
SUB=$(curl -sS https://api.netmind.ai/v1/generation \
  -H "Authorization: Bearer $NETMIND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "google/veo3.1-fast",
    "config": {
      "prompt": "YOUR DETAILED VIDEO PROMPT — describe motion, camera, subject, atmosphere"
    }
  }')
JOB_ID=$(echo "$SUB" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "Submitted video job $JOB_ID"
```

### Step 2 — Poll (longer than images)

```bash
for i in $(seq 1 90); do  # ~3 minutes max
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
  sleep 4   # poll every 4s — video is slower than image
done
```

Video jobs typically complete in 30-90 seconds.

### Step 3 — Download

```bash
URL=$(echo "$POLL" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['data'][0]['url'])")
mkdir -p assets
curl -sSL -o assets/hero-loop.mp4 "$URL"
echo "Saved assets/hero-loop.mp4"
```

The video comes back as `.mp4`.

## HTML embedding (for hero loops)

```html
<video autoplay muted loop playsinline poster="assets/hero-poster.jpg">
  <source src="assets/hero-loop.mp4" type="video/mp4">
</video>
```

- `autoplay muted loop playsinline` — mandatory combo for mobile background loops
- Always provide a `poster` (a still image as fallback during load) — use `netmind-image-gen` to make one
- Background video should be **silent and atmospheric**, not narrative

## Prompt formula

```
[Subject + action] [Camera motion] [Lighting/atmosphere] [Style cue]
```

### Good video prompts

```
Aerial drone shot slowly orbiting a futuristic glass office building at golden hour,
cinematic color grading, warm orange glow on the facade, slow steady motion
```

```
Macro slow-motion shot of liquid coffee being poured into a white ceramic cup,
soft studio lighting from above, crisp focus on the splash, 4K cinematic quality
```

### Critical rules

- **Be explicit about MOTION** — diffusion video models need to know what's moving and how
- **Specify camera**: "static shot", "slow pan left", "orbit", "push-in", "aerial drone"
- **Avoid people speaking** — lip sync is bad on fast video models
- **Avoid text in scene** — same as images

## Saving conventions

- Save into `./assets/` inside the agent workspace
- Filenames: `hero-loop.mp4`, `product-demo.mp4`
- Always pair with a poster image (use `netmind-image-gen`) named `assets/<video>-poster.jpg`

## Cost discipline

- One video gen ≈ 5-10× a single image gen in tokens and time
- For a typical landing page, you usually need **0** videos (a strong image + good motion CSS beats a generated video)
- If unsure, **skip the video** and tell PM "I judged a static hero was better here — generate a video pass later if you want"

## Anti-patterns — do NOT do

- ❌ Do not echo `$NETMIND_API_KEY`
- ❌ Do not use video for sites where motion adds no value
- ❌ Do not generate a 30s background loop when 4s loops fine
- ❌ Do not skip the poster image — visitors see broken-frame thumbnails during load
