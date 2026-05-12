<!--
Draft: Structure A (Dev-focused) — English — A3 hook
Date: 2026-05-12
Notes:
  - Audience: developers, tech-savvy evaluators, integrators
  - Tone: concept-based with some emotion (not pure tech)
  - 3 traits framed as "Capabilities" with dev language:
      (1) Agents like real teammates (Awareness + Narrative memory + MCP tools)
      (2) Inter-agent collaboration (MessageBus)
      (3) Batteries included (10 modules + 3 deploy paths + multi-LLM)
  - Hero: AI Manga Explanation (same as B+), placeholder = weather GIF
  - Templates: same 3 as B+ (synced)
  - Explicit callout: single-agent paradigm (OpenClaw/ZeroClaw) vs multi-agent team
-->

<div align="center">

<img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

# Don't deploy an agent. Launch a team.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#community)

**English** | [中文](./README_A.zh.md)

</div>

---

<br/>

<p align="center">
  <em>Agents that already know how to remember, collaborate, and use tools — start from a template, or compose your own.</em>
</p>

<!-- TODO: replace showcase-weather.gif placeholder with PLACEHOLDER_hero_manga.gif once recorded -->
<p align="center">
  <img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/images/showcase-weather.gif" alt="Upload one chapter of manga. Get a narrated video in 90 seconds." width="760" />
</p>

<p align="center">
  <em>Upload one chapter of manga.<br/>
  90 seconds later: a YouTube / Bilibili-style narrated video.<br/>
  <sub>(4 agents in sequence: parse · narrate · score · render)</sub></em>
</p>

<p align="center">
  <a href="#templates">See more templates →</a>
</p>

<br/>

---

##  Get started in 60 seconds

NarraNexus is a multi-agent product — not yet another framework where you wire agents together, but a ready-to-run team of agents that already collaborate. Every agent has persistent identity, built-in communication channels, and three deployment paths to choose from.

Cloud sign-up, desktop download, local build — pick whichever suits you.

### 🛠️ From source (recommended for developers)

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` checks for `uv` / `node` / `tmux`, then launches 7 services:

| Service | Port | What it does |
|---------|------|--------------|
| Backend | 8000 | FastAPI server |
| DB Proxy | 8100 | SQLite HTTP proxy |
| MCP servers | 7801+ | Per-module tool servers |
| Frontend | 5173 | Vite dev server |
| Poller | — | Instance state monitor |
| Jobs Trigger | — | Scheduled job dispatcher |
| BusTrigger | — | Inter-agent message bus |

For detailed setup, see the [dev docs](https://website.narra.nexus/docs/getting-started/quick-start).

<!-- TODO: local build demo video, ~30s -->
<p align="center">
  <em>📽️ Local build demo — TBD</em>
</p>

### 💻 macOS desktop app

1. [Download the `.dmg`](https://github.com/NetMindAI-Open/NarraNexus/releases/latest)
2. Drag it into the Applications folder
3. Launch → pick a template and go

<!-- TODO: dmg install demo video, ~30s -->
<p align="center">
  <em>📽️ macOS desktop install demo — TBD</em>
</p>

### ☁️ Cloud sign-up

1. Open [agent.narra.nexus](https://agent.narra.nexus/login)
2. Sign up
3. Pick a template and go

<!-- TODO: cloud sign-up demo video, ~30s -->
<p align="center">
  <em>📽️ Cloud sign-up demo — TBD</em>
</p>

> Three doors, same place.

---

##  Three Core Capabilities

What an agent actually does for you.

### Agents like real teammates

Every agent has persistent identity and preferences (managed by the **Awareness module**), so it remembers who it is and who it's working for across sessions. Conversations are auto-routed into storylines — **Narrative memory** uses embedding-based topic retrieval, not naive chronological order. Every agent can call MCP tools; installing a new skill from [ClawHub](https://website.narra.nexus/docs/modules/skills) is one chat line away — no code change.

### Agents that actually collaborate

Not just chat — agents talk to each other over the built-in **MessageBus** protocol: @mentions, rooms, group chats. The framework includes rate limiting and poison-message detection to prevent agent loops from going off the rails. Agents are discoverable by capability — one needs a SQL-savvy helper, it finds one with a search.

### Batteries included

**10 built-in modules** ready to use: Memory · Awareness · Chat · SocialNetwork · Jobs · Skills · MessageBus · Lark · CommonTools · BasicInfo. Each module ships with its own DB schema, MCP tools, and lifecycle hooks. **Multi-LLM** support (Anthropic / OpenAI / Gemini) through a unified adapter. **4 trigger modes** (Chat / Job / MessageBus / Matrix·Lark) share the same 6-step pipeline.

---

<a name="templates"></a>

##  Reference Templates

Reference implementations — use them directly, or fork to customize.

### Financial Morning Brief

For investors and analysts who read markets at 7am. **8 agents** — global market monitor, macro calendar, news filter, cross-asset reasoning, portfolio mapper, sector themes, charts, chief strategist. They answer *"what is the market trading today, and should I attack, defend, or watch?"* — not yet another news summary.

<!-- TODO: morning brief template demo video, ~30s -->
<p align="center">
  <em>📽️ Financial Morning Brief demo — TBD</em>
</p>

### Sales Agent Team

For solo founders and small sales teams. One instruction kicks off a sales team — you talk to just one agent, the rest of the team handles the back-office work: multi-channel outreach, sorting daily replies, updating customer state.

<!-- TODO: Sales team template demo video, ~30s -->
<p align="center">
  <em>📽️ Sales Agent Team demo — TBD</em>
</p>

### One-click migration from OpenClaw / Hermes / Claude Code

Already using another AI tool? Two clicks bring your existing OpenClaw / Hermes / Claude Code agents over into a NarraNexus team — the setup and history you've already built come with them.

<!-- TODO: migration template demo video, ~30s -->
<p align="center">
  <em>📽️ One-click migration demo — TBD</em>
</p>

### More community templates → [browse all](https://website.narra.nexus/docs/modules/custom-modules)

> *All built by NarraNexus agents themselves.*

---

##  Honest Limitations

- **LLM API key**: the cloud version has a free trial quota. For daily or local use, you'll want your own LLM API key — one or two minutes to register at NetMind, OpenAI, or Anthropic.
- **An agent isn't 100% out of the box**: it needs your corrections and feedback to get sharp. Treat it like a new hire, not a god.
- **Collaboration isn't perfect on the first try**: complex tasks may take a couple of iterations before agents get the hang of it; some judgment calls always belong to you — the team handles the bulk, the key calls stay with you.
- **Architecture trade-off**: each agent launches its own MCP process, adding ~100ms of startup overhead. Negligible for chat-style workflows; switch to Direct Trigger mode for high-frequency jobs.

###  How we compare

| If you want… | Use… | Why |
|--------------|------|------|
| Python primitives to compose agents | LangChain · AutoGen · CrewAI | Library-level building blocks, no opinionated runtime |
| One personal assistant across channels | OpenClaw · ZeroClaw · Claude Code | Single-agent, multi-channel paradigm |
| **A team of agents that collaborate, remember, and ship real work** | **NarraNexus** | Opinionated runtime + hot-swappable modules + multi-agent collaboration |

We don't replace those tools. We solve a different problem: **not just a personal assistant — an agent team.**

---

##  Community

<a name="community"></a>

- **Discord** — `coming soon`
- **Twitter / X** — `coming soon`
- **Email updates** — `coming soon`
- **Feedback** — [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

---

##  Contributing

### I made a template I want to share
Pack it into a `.nxbundle` and submit via any of these:

- 💬 **Discord** — `coming soon`
- 📧 **Email** — `coming soon`
- 🐦 **Twitter / X DM** — `coming soon`
- 🐛 **GitHub Issue** — [open an issue](https://github.com/NetMindAI-Open/NarraNexus/issues) (prefix the title with `[template]`)

We review and add accepted ones to the official template library.

### I want to build a Module
New modules subclass `XYZBaseModule`, register in `MODULE_MAP`, and ship their own schema + repository. See the [new-module guide](https://website.narra.nexus/docs/contributing/development-setup).

### I want to change the code
- **Bug reports** → [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)
- **New features** → open an issue to discuss direction first, then PR
- **Roadmap** → `coming soon`

---

## License

[CC BY-NC 4.0](./LICENSE)

<!--
Alternative hooks (kept for record):

Current (A3):
  - "Don't deploy an agent. Launch a team."  ← CURRENT (A3-en)
  - "别只部署一个 agent。直接组一支团队。"  ← A3-zh counterpart

Other candidates considered:
  - A1: "An agent team, one click away."
  - A2: "Launch your agent team in one click."
  - A4: "One click. A whole agent team. Online."
  - A5: "Beyond single-agent assistants. An agent team, one click away."
  - A6: "A multi-agent product. Launch in one click."

Merged hook (for boss's review):
  "Most agent tools are built for developers. NarraNexus is built for everyone else."

PLACEHOLDER ASSETS NEEDED:
  - docs/images/PLACEHOLDER_hero_manga.gif (30s manga explanation demo)
  - docs/images/PLACEHOLDER_install_local.gif (~30s local build flow)
  - docs/images/PLACEHOLDER_install_dmg.gif (~30s macOS dmg install flow)
  - docs/images/PLACEHOLDER_install_cloud.gif (~30s cloud sign-up flow)
  - docs/images/PLACEHOLDER_template_morning_brief.gif
  - docs/images/PLACEHOLDER_template_sales_team.gif
  - docs/images/PLACEHOLDER_template_openclaw_migrate.gif
-->
