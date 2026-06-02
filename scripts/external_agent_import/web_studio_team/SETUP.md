# Static Site Build Team — SETUP

A generic 5-agent team that builds a single-page static website from any brief in ~1 hour. Works for: landing pages, marketing sites, event companions, product microsites, portfolio pages. User talks to the **Project Manager**; PM dispatches Content + Visual + Web Designer + QA on a shared MessageBus channel.

This file walks you through **one-time setup** before your first run.

---

## 0. Import the template

`static_site_build_team.nxbundle` is a 5-agent NarraNexus bundle. Import the same way as any other:

- **Cloud** (`agent.narra.nexus`): Settings → **Import bundle** → drag in `static_site_build_team.nxbundle`.
- **Desktop / self-hosted**: Settings → Import bundle → drag in.

After import you'll see the team **Static Site Build Team** with 5 agents:
Project Manager · Web Designer · Content Creator · Visual · QA Reviewer.

---

## 1. MCP servers — install ONCE, reusable across all projects

The team's skills are markdown playbooks that tell each agent **how** to use a tool. The tool itself is provided by an MCP server you set up here.

### 1.1 Playwright MCP (recommended — preview + screenshots + a11y inspection)

Used by **Web Designer** (self-preview) and **QA Reviewer** (3-breakpoint screenshots + accessibility-tree audit).

**Desktop / local NarraNexus** (stdio mode):
```bash
claude mcp add playwright npx @playwright/mcp@latest
```

**Cloud-hosted NarraNexus** (SSE mode — run on the cloud host once, add URL in NarraNexus's MCPManager):
```bash
# on the cloud host, e.g. via pm2 or systemd
pm2 start "npx @playwright/mcp@latest --port 8931" --name playwright-mcp
# Then in NarraNexus UI: Settings -> MCP -> Add -> name: playwright, url: http://localhost:8931/sse
```

No API key. First run downloads Chromium (~150 MB). Operates on the **accessibility tree** (text, not pixels) — cheap, reliable, deterministic.

### 1.2 Image-generation MCP (optional — Visual falls back to writing briefs if absent)

Used by **Visual**. Pick ONE backend:

**Path A — Google Gemini / Nano Banana** (recommended; free tier covers POCs)
```bash
export GEMINI_API_KEY="..."   # https://aistudio.google.com/apikey
# Install a Gemini image-gen MCP (e.g. lansespirit/image-gen-mcp or guinacio/claude-image-gen)
```

**Path B — OpenAI gpt-image** (best in-image typography — pay-per-image, ~$0.005-$0.40)
```bash
export OPENAI_API_KEY="..."
```

**Path C — Fal.ai (Flux Pro / Schnell)** (best for brand-consistent batches)
```bash
export FAL_KEY="..."
```

**Skip-image mode**: if you don't install any image-gen MCP, **Visual will deliver image briefs** instead — detailed prompts + aspect ratios + style notes — and you generate them externally (Midjourney, Nano Banana, etc.) and drop the files into `./assets/`. This is fine for a POC; the rest of the team still works.

### 1.3 Web search (built-in; optional upgrade to Tavily/Exa)

Used by **PM, Content, Visual, QA** to verify any specific claim. Built-in `WebSearch` on Claude / NarraNexus is the default and needs no setup. For higher-quality search:

```bash
export TAVILY_API_KEY="..."   # tavily.com (free tier)
claude mcp add tavily npx -y @tavily/mcp-server
# or Exa
export EXA_API_KEY="..."
claude mcp add exa npx -y exa-mcp-server
```

---

## 2. Your LLM provider

Normal NarraNexus setup:

- Configure your **Agent / Embedding / Helper LLM** slots in Settings → Providers
- The PM does the heavy orchestration — **Claude Sonnet 4.6 minimum**, Opus 4.7 ideal
- Token spend per full build: **~$0.50 – $2.00** (heaviest cost is image-gen if you enabled it)

---

## 3. The shared workspace

By convention, the team coordinates around the PM's workspace. NarraNexus places it at:

```
~/.nexusagent/workspaces/<pm_agent_id>_<user_id>/
```

Final output after a build:
```
<workspace>/
├── project_brief.md       ← PM writes this from your first message; team reads it as canonical
├── index.html             ← Designer's output
├── style.css              ← optional, only if Tailwind can't do it
├── script.js              ← optional, the ONE interactive component
├── assets/                ← Visual saves images here
│   ├── hero.jpg
│   ├── <section>.jpg
│   └── og-share.jpg
└── qa/
    ├── mobile.png
    ├── tablet.png
    ├── desktop.png
    └── report.md          ← QA's prioritized fix list
```

Preview locally:
```bash
cd ~/.nexusagent/workspaces/<pm_agent_id>_<user_id>
python3 -m http.server 8000
# Open http://localhost:8000
```

---

## 4. First message to the PM (the 1-hour flow)

Open a chat with **Project Manager** and write your brief. Anything from a one-liner to a paragraph works. Examples:

> Build a marketing site for our AI tool launch next month. Premium feel. Email capture above the fold.

> Build a companion site for "Acme Conf 2026" (Sept 12-14). Audience: people who couldn't get a ticket. Include schedule, speaker list (you'll find from acmeconf.com), and a livestream signup.

> Build a portfolio page for me — I'm a UX designer with 6 years experience, looking for senior roles at consumer companies. Confident, minimalist, with 3 case study links.

What you'll see, in order:

1. **PM restates the brief** in 3-5 lines (sanity check) and confirms or asks at most 1 clarifying question.
2. **PM writes `project_brief.md`** to the workspace — that's now the canonical source-of-truth.
3. **PM dispatches** Content + Visual in parallel, then Designer, then QA — all in the `web-build-coordination` channel.
4. You get **brief status updates** (not the full team chatter).
5. **PM delivers** with: the URL (typically `http://localhost:8000`), what's done, what's deferred, any decisions pending.

At any point you can @-mention a teammate directly:
- `@Web Designer make the hero full-bleed`
- `@Visual swap the hero — make it cooler, more cinematic`
- `@Content the second CTA should be softer, less salesy`

The teammate will respond directly when @-mentioned; otherwise they reply to the PM in the team channel.

---

## 5. Quick troubleshooting

| Symptom | Fix |
|---|---|
| PM "narrates" delegation but no teammates run | The PM is supposed to call `bus_send_message`. If it's not, message it: "use `bus_send_message` to actually post to `web-build-coordination` — writing @-mentions in your reply is just narration." |
| Visual says "no image-gen tool available" | Either install an image-gen MCP (§1.2) or accept Visual's image-brief fallback and generate externally |
| QA says "can't navigate to localhost:8000" | Designer didn't start the server — message Designer: "start a local server with `python3 -m http.server 8000` from the workspace and tell QA again" |
| Site looks identical at all breakpoints | Designer missed mobile-first — say "Designer, redo with mobile-first Tailwind ordering" |
| Hero has weird in-image text | Hero shouldn't have text. Tell Visual: "regenerate hero with `no in-image text` in the prompt tail" |
| Content invented a fact (event, person, partner) | Tell Content: "scrub any specific claim that wasn't web-search verified — replace with `[unverified]` or remove" |

---

## 6. Cost expectations (one full build)

| Item | Estimate |
|---|---|
| LLM tokens (PM orchestration + 4 teammates) | $0.50 – $2.00 |
| Image-gen (1 hero quality + 3-4 drafts + 1 OG quality) | ~$1.00 with premium models; <$0.10 with Gemini free tier; $0 if skipping |
| Playwright (text-based, no API) | $0 |
| **Total** | **~$0.50 – $3.00 per build** |

---

## 7. License + attribution

Authored by the **NarraNexus team**. **MIT** license.

The 7 packaged skills (project-brief-template, web-search-guide, html-tailwind-essentials, playwright-mcp, image-gen-mcp, copywriting-essentials, accessibility-essentials) are original content for this template — also MIT.
