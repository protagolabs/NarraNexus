<!--
Draft: Structure B+ (fresh user focused) — English — Hook in B2 style
Date: 2026-05-12
Notes:
  - Synced to B+.zh v2 (cumulative rounds 1-7)
  - First three screens: zero technical explanation
  - Hero demo placeholder = weather GIF; manga GIF TODO
  - Templates at section 7 (per user); hero block has "more →" link
  - Templates: Financial Morning Brief / AI Manga Explanation (Hero) / Sales Agent Team / Migration from OpenClaw·Hermes·Claude Code
  - Alternative hooks archived in HTML comment at bottom
-->

<div align="center">

<img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

# You bring the ideas. The AI team delivers.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#community)

**English** | [中文](./README_Bplus.zh.md)

</div>

---

<br/>

<!-- TODO: replace showcase-weather.gif placeholder with PLACEHOLDER_hero_manga.gif (manga input → narrated output, ~30s) once recorded -->
<p align="center">
  <img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/images/showcase-weather.gif" alt="Upload one chapter of manga. Get back a YouTube/Bilibili-style narrated video." width="760" />
</p>

<p align="center">
  <em>Upload one chapter of manga.<br/>
  90 seconds later: a YouTube / Bilibili-style narrated video.</em>
</p>

<p align="center">
  <a href="#templates">See more templates →</a>
</p>

<br/>

---

##  Get started in 60 seconds

NarraNexus lets you use a ready-made AI team — or build your own from scratch.
Ready templates put you in business in seconds; building your own is only a few more steps.

Cloud sign-up, desktop download, local build — pick whichever suits you.

### ☁️ Cloud (fastest)

1. Open [agent.narra.nexus](https://agent.narra.nexus/login)
2. Sign up
3. Pick a template and go

<!-- TODO: cloud sign-up demo video, ~30s -->
<p align="center">
  <em>📽️ Cloud sign-up demo — TBD</em>
</p>

### 💻 macOS desktop app

1. [Download the `.dmg`](https://github.com/NetMindAI-Open/NarraNexus/releases/latest)
2. Drag it into the Applications folder
3. Launch → pick a template and go

<!-- TODO: dmg install demo video, ~30s -->
<p align="center">
  <em>📽️ macOS desktop install demo — TBD</em>
</p>

### 🛠️ Local build (developers)

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

For detailed setup, see the [dev docs](https://website.narra.nexus/docs/getting-started/quick-start).

<p align="center">
  <video src="../docs/videos/install-local.mp4" controls width="720">
    Your browser does not support the video tag. <a href="../docs/videos/install-local.mp4">Download the demo (MP4)</a>.
  </video>
</p>

> Three doors, same place.

---

##  How it works

Three things make this different from a chatbot.

**1. They remember.**
Not just the last message — the whole storyline. The agent who summarized your week last Friday will pick up where it left off this Friday.

**2. They divide the work.**
A task needs research + writing + visuals? They hand off and finish it together — you don't have to break it down. The result lands on your side already assembled.

**3. They stand on other people's work.**
Don't know how to do something? Reuse a template someone has already validated. Missing a capability? Let the agent learn it on its own — you don't have to teach.

> Under the hood there's a runtime, a context engine, a module system, MCP, a hook manager. You'll never touch any of it. [Read the docs](https://website.narra.nexus/docs/core-concepts/architecture) if you want to.

---

<a name="templates"></a>

##  Templates — real teams, real work

The Hero above is one of them. Below are more.

###  Financial Morning Brief

For investors and analysts who read markets at 7am. **8 agents** — global market monitor, macro calendar, news filter, cross-asset reasoning, portfolio mapper, sector themes, charts, chief strategist. They answer *"what is the market trading today, and should I attack, defend, or watch?"* — not yet another news summary.

<!-- TODO: morning brief template demo video, ~30s -->
<p align="center">
  <em>📽️ Financial Morning Brief demo — TBD</em>
</p>

###  Sales Agent Team

For solo founders and small sales teams. One instruction kicks off a sales team — you talk to just one agent, the rest of the team handles the back-office work: multi-channel outreach, sorting daily replies, updating customer state.

<!-- TODO: Sales team template demo video, ~30s -->
<p align="center">
  <em>📽️ Sales Agent Team demo — TBD</em>
</p>

###  One-click migration from OpenClaw / Hermes / Claude Code

Already using another AI tool? Two clicks bring your existing OpenClaw / Hermes / Claude Code agents over into a NarraNexus team — the setup and history you've already built come with them.

<!-- TODO: migration template demo video, ~30s -->
<p align="center">
  <em>📽️ One-click migration demo — TBD</em>
</p>

###  More community templates → [browse all](https://website.narra.nexus/docs/modules/custom-modules)

> *All built by NarraNexus agents themselves.*

---

##  Honest limitations

We'd rather tell you up-front.

- **LLM API key**: the cloud version has a free trial quota. For daily or local use, you'll want your own LLM API key — one or two minutes to register at NetMind, OpenAI, or Anthropic.
- **An agent isn't 100% out of the box**: it needs your corrections and feedback to get sharp. Treat it like a new hire, not a god.
- **Collaboration isn't perfect on the first try**: complex tasks may take a couple of iterations before agents get the hang of it; some judgment calls always belong to you — the team handles the bulk, the key calls stay with you.

###  How we compare

| If you want… | Use… |
|--------------|------|
| To build agents in Python | LangChain · AutoGen · CrewAI |
| One personal assistant across your channels | OpenClaw · ZeroClaw · Claude Code |
| **A team of agents that collaborate, remember, and ship real work** | **NarraNexus** |

We don't replace those tools. We solve a different problem: **building agents is no longer the bottleneck. Putting an AI team to real work is.**

---

##  Community

<a name="community"></a>

- **Discord** — `coming soon`
- **Twitter / X** — `coming soon`
- **Email updates** — `coming soon`
- **Feedback** — [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

---

##  Contributing

### I'm a user with a template to share
Made an agent team that helps other people? Pack it into a `.nxbundle` and submit via any of these:

- 💬 **Discord** — `coming soon`
- 📧 **Email** — `coming soon`
- 🐦 **Twitter / X DM** — `coming soon`
- 🐛 **GitHub Issue** — [open an issue](https://github.com/NetMindAI-Open/NarraNexus/issues) (prefix the title with `[template]`)

We review and add accepted ones to the official template library.

### I'm a developer who wants to change the code
- **Bug reports** → [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)
- **New features** → open an issue to discuss direction first, then PR
- **New modules** → see the [development guide](https://website.narra.nexus/docs/contributing/development-setup)
- **Roadmap** → `coming soon`

---

## License

[CC BY-NC 4.0](./LICENSE)

<!--
Alternative hooks (kept for record, in case we want to swap):

Direction A · clean division (parallel role split):
  - "You bring the ideas. The AI team delivers."  ← CURRENT (B2-en, V1)
  - "Your ideas. Their execution."
  - "You think it. They ship it."

Direction B · soft / aspirational:
  - "Your next teammate doesn't have to be human."  (original v1 hook, less product-focused)
  - "Ideas in. Results out."

Direction C · Huashu-style 3-beat (action-action-result):
  - "Bring an idea. Say a sentence. The AI team delivers."

Merged hook (for boss's review):
  "Most agent tools are built for developers. NarraNexus is built for everyone else."

PLACEHOLDER ASSETS NEEDED:
  - docs/images/PLACEHOLDER_hero_manga.gif (30s manga explanation demo — THE most important visual asset)
  - docs/images/PLACEHOLDER_install_cloud.gif (~30s cloud sign-up flow)
  - docs/images/PLACEHOLDER_install_dmg.gif (~30s macOS dmg install flow)
  - docs/images/install-local.gif ✅ (recorded — local build flow)
  - docs/images/PLACEHOLDER_template_morning_brief.gif (~30s morning brief demo)
  - docs/images/PLACEHOLDER_template_sales_team.gif (~30s sales team demo)
  - docs/images/PLACEHOLDER_template_openclaw_migrate.gif (~30s migration demo)
-->
