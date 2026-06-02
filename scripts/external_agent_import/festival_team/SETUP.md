# Festival Site Team — SETUP

A 5-agent NarraNexus team that ships a single-page companion website for the Great Exhibition Road Festival (**6–7 June 2026**, South Kensington, free) in **about an hour**. Fresh user talks to the **Festival Producer**; the producer dispatches Content / Visual / Web Designer / QA.

This file walks you through the **one-time setup** before your first run.

---

## 0. Import the template

`festival_site_team.nxbundle` is a multi-agent NarraNexus bundle. Import it the same way as any other:

- **Cloud** (`agent.narra.nexus`): Settings → **Import bundle** → drag in `festival_site_team.nxbundle`.
- **Desktop / self-hosted**: Settings → Import bundle → drag in, OR `POST /api/bundle/import` from a script.

After import you'll see the team **Festival Site Team** with 5 agents:
Festival Producer · Web Designer · Content Creator · Visual · QA Reviewer.

---

## 1. MCP servers (the actual "doers")

The team's skills are markdown **playbooks** that tell each agent *how* to use a tool. The tool itself is provided by an MCP server you install once. Two are required for full effect; one is optional.

### 1.1 Playwright MCP (required — preview + screenshots + a11y inspection)

Used by **Web Designer** (self-preview) and **QA Reviewer** (responsive screenshots + accessibility tree).

```bash
claude mcp add playwright npx @playwright/mcp@latest
```

- No API key.
- First run will download Chromium (~150 MB) — let it finish.
- Operates on the page's **accessibility tree** (text), not pixels — cheap + reliable.

### 1.2 Image-generation MCP (required if you want generated imagery)

Used by **Visual**. Pick ONE backend — easiest first:

**Path A — Google Gemini / Nano Banana** (recommended for the POC; free tier covers a demo)

```bash
# Get a free key from https://aistudio.google.com/apikey
export GEMINI_API_KEY="..."
# Install an image-gen MCP that targets Gemini (Nano Banana / Imagen)
claude mcp add gemini-image npx -y @your-favorite-org/image-gen-mcp-gemini
```

Substitute whichever Gemini-image MCP package you trust. Several exist on GitHub; `lansespirit/image-gen-mcp` and `guinacio/claude-image-gen` both ship Gemini support.

**Path B — OpenAI gpt-image** (better in-image text — pay-per-image)

```bash
export OPENAI_API_KEY="..."
# Install an MCP that targets gpt-image-1.5 / dall-e-3
claude mcp add openai-image npx -y @lansespirit/image-gen-mcp
```

Cost: ~$0.005 (low) to ~$0.40 (high-quality 4K) per image.

**Path C — Fal.ai (Flux Pro / Schnell)** — when you want brand-consistent batches

```bash
export FAL_KEY="..."
# Install fal-ai MCP
claude mcp add fal-image npx -y @fal-ai/mcp-server
```

The `image-gen-mcp` SKILL.md inside the bundle assumes whichever you installed — just tell Visual "use the configured image-gen MCP" and it'll discover the tools.

> 💡 If you skip image-gen entirely, the **Visual** agent will hand you ready-to-go image briefs (resolutions, prompts, descriptions) and the Designer can ship with `unsplash.com` placeholders or skip imagery for the POC.

### 1.3 Web search MCP (optional — built-in WebSearch is the fallback)

Used by **PM** + **Content Creator** to verify festival facts.

- **Easiest path**: do nothing. Claude (the LLM behind your NarraNexus agents) has a built-in `WebSearch` tool on most providers. The `web-search-guide` skill instructs the agents to use it first.
- **For higher search quality**: install Tavily or Exa:

```bash
# Tavily — most common Claude-friendly web search
export TAVILY_API_KEY="..."   # tavily.com (free tier)
claude mcp add tavily npx -y @tavily/mcp-server

# Or Exa
export EXA_API_KEY="..."      # exa.ai
claude mcp add exa npx -y exa-mcp-server
```

---

## 2. Your LLM provider

This is just normal NarraNexus setup:

- Configure your **Agent / Embedding / Helper LLM** slots in Settings → Providers
- The agents work on any Claude-family model (Sonnet 4.5+ recommended; Opus 4.7 ideal for the PM)
- ~$0.5–$2 of token spend per full team run (the heaviest costs are image-gen, not text)

---

## 3. Your output directory

The Designer writes files to a workspace folder. By default NarraNexus puts each agent's workspace under:

```
~/.nexusagent/workspaces/<agent_id>_<user_id>/
```

The team will collaborate by all writing into **one** of those workspaces — by convention the **Web Designer's** workspace. The PM will tell each agent to use that path.

After the run, you'll find:
```
<workspace>/
├── index.html
├── style.css   (optional)
├── script.js   (optional, one interactive component)
├── assets/
│   ├── hero.jpg
│   ├── zone-science.jpg
│   ├── zone-arts.jpg
│   ├── zone-family.jpg
│   └── og-share.jpg
└── qa/
    ├── mobile.png
    ├── tablet.png
    ├── desktop.png
    └── report.md
```

To preview locally:
```bash
cd ~/.nexusagent/workspaces/<designer_agent_id>_<user_id>
python3 -m http.server 8000
# Open http://localhost:8000
```

---

## 4. First message to the Producer (the 1-hour flow)

Open a chat with **Festival Producer** and paste something like:

> Build a companion website for the **Great Exhibition Road Festival 2026** (6–7 June, South Kensington, free) for people who can't physically attend. Friendly, informative, with a zone overview of what's happening at each museum partner — Natural History Museum, Science Museum, V&A, Royal Albert Hall. Include a 2-day schedule overview, a "what to expect" section, and contact / share links.

What you'll see:

1. **PM restates the brief** (3–5 lines) + **drops a PRD/sitemap**
2. **PM dispatches** Content Creator + Visual in parallel, then Web Designer, then QA
3. **You get status updates only** — the team chatter stays in the team room
4. **You get a delivery message**: "Site is at http://localhost:8000 — QA-cleared with 0 BLOCKERs. 2 HIGH items to consider for v2: …"
5. (Optional) **You @mention any teammate** — `@Web Designer make the hero full-bleed`, `@Visual swap the family zone for a science-fair shot`, etc.

---

## 5. Quick troubleshooting

| Symptom | Fix |
|---|---|
| Visual says "no image-gen tool available" | Install one of the MCPs in §1.2 + restart the cloud/desktop session |
| QA says "can't navigate to localhost:8000" | Designer didn't start the server yet — `cd <workspace>; python3 -m http.server 8000` |
| Agents quote 2025 dates | Tell PM "remind the team the festival is 6–7 June **2026**" — they'll re-check `festival-fact-sheet` |
| Site looks identical at all breakpoints | Designer missed mobile-first — say "Designer, redo with mobile-first Tailwind ordering" |
| Hero image has a giant typo | Hero shouldn't have text; if it does, Visual put text on the wrong asset. Tell Visual "regenerate hero with `no in-image text` prompt tail" |
| Content invented a named event | Tell Content "scrub any names that didn't come from web-search verification" |

---

## 6. Cost expectations (one full POC run)

| Item | Estimate |
|---|---|
| LLM tokens (5 agents, ~1 hour of orchestration) | $0.50 – $2.00 |
| Image-gen (1 hero quality + 4 drafts + 1 OG quality) | ~$1.00 if using premium models; <$0.10 with Gemini free tier |
| Playwright (text-based MCP) | $0 |
| **Total** | **~$0.50 – $3.00 per build** |

---

## 7. License + attribution

This template was authored by the **NarraNexus team** for the 2026-06 demo activity. **MIT** license.

The 7 packaged skills (festival-fact-sheet, web-search-guide, html-tailwind-essentials, playwright-mcp, image-gen-mcp, copywriting-essentials, accessibility-essentials) are also MIT — original content for this template, not imported.
