<!--
Draft: Structure A+ (Dev-focused, governance-aware) — English
Date: 2026-05-28
Variant of README_A — adopts a colleague's advice on the contribution section
(see local note: README_advice.md, advice items #1, #3, #4, #6). Clone-URL
advice (#2) intentionally skipped per the user.

Changes vs README_A.en.md:
  - First-screen "Found a bug / need help?" sub-line under the lang switch (#6)
  - "Building with an AI coding assistant?" callout between hero and Get-Started (#1)
  - "## Contributing" rewritten as "## Contributing & governance" — navigation
    only, links to AGENTS.md / CLAUDE.md / GOVERNANCE.md / MAINTAINERS.md /
    CODE_OF_CONDUCT.md / SECURITY.md / .mindflow/_overview.md, ends with
    git-shortlog line (#3 + #4). Verbose "I made a template / Module / change
    the code" sub-sections collapsed — those belong in CONTRIBUTING.md.
-->

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../docs/images/NarraNexusLogo_v2/narra-nexus-logo-text-dark-mode.svg">
  <img src="../docs/images/NarraNexusLogo_v2/narra-nexus-logo-text-light-mode.svg" alt="NarraNexus" width="480" />
</picture>

<br/>
<br/>

# Don't deploy an agent from scratch. Collaborate with a professional team in one click.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#community)

**English** | [中文](./README_Aplus.zh.md)

<br/>
<sub>Found a bug or need help? · <a href="https://github.com/NetMindAI-Open/NarraNexus/issues/new/choose">Open an issue</a> · <a href="https://github.com/NetMindAI-Open/NarraNexus/discussions">Discussions</a></sub>

</div>

---

<br/>

<p align="center">
  <em>Agents that already know how to remember, collaborate, and use tools — start from a template, or compose your own.</em>
</p>

<p align="center">
  <img src="../docs/images/hero-intro.gif" alt="A 90-second tour of NarraNexus — install, core concepts, and templates in action." width="760" />
</p>

<p align="center">
  <em>A 90-second tour — install, core concepts, and templates in action.</em>
</p>

<p align="center">
  <a href="#templates">See more templates →</a>
</p>

<p align="center">
  <em>Building with an AI coding assistant?</em><br/>
  Drop <a href="./CLAUDE.md"><code>CLAUDE.md</code></a> (binding rules) and
  <a href="./.mindflow/_overview.md"><code>.mindflow/_overview.md</code></a> (mirror-doc index)
  into Claude Code, Cursor, or Continue —<br/>
  your agent will follow our conventions out of the box.
  Full guide: <a href="./CONTRIBUTING.md"><code>CONTRIBUTING.md</code></a>.
</p>

<br/>

---

##  Get started in 60 seconds

NarraNexus is a multi-agent product — not yet another framework where you wire agents together, but a ready-to-run team of agents that already collaborate. Three deployment paths — pick whichever suits you.

### ☁️ Cloud sign-up — fastest, with a free trial quota

1. Open [agent.narra.nexus](https://agent.narra.nexus/login)
2. Sign up
3. Pick a template and go

<!-- TODO: cloud sign-up demo video, ~30s -->

> [!NOTE]
> **Running locally (desktop app or source)?** Two things to know:
> - **Bring your own LLM API key.** The desktop app and local build run on your own key — use a Claude Code login, or grab a NetMind.AI Power key (one key, takes a minute). Configure it under **Settings** — see [Configure LLM Providers](https://narra.nexus/docs/getting-started/quick-start).
> - **Free up local ports.** Both run several local services; make sure those ports aren't already taken.

### 💻 macOS desktop app

The app bundles its own runtime — no Python / Node / Docker to install.

1. [Download the app](https://github.com/NetMindAI-Open/NarraNexus/releases/latest)
2. Drag it into the Applications folder
3. Launch → pick a template and go

<!-- TODO: dmg install demo video, ~30s -->

### 🛠️ From source (developers)

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` checks prerequisites (`uv` / `node` / `tmux`) and launches all local services. For the full service/port list and detailed setup, see the [dev docs](https://narra.nexus/docs/getting-started/quick-start).

<p align="center">
  <video src="../docs/videos/install-local.mp4" controls width="720">
    Your browser does not support the video tag. <a href="../docs/videos/install-local.mp4">Download the demo (MP4)</a>.
  </video>
</p>

> Three doors, same place.

---

##  Three Core Features

### What an agent actually does for you.

<p align="center">
  <img width="1672" height="941" alt="all-v4" src="https://github.com/user-attachments/assets/52eeb6b3-c190-486c-ad4a-49bd7d7959b7" />
</p>


---

<a name="templates"></a>

##  Reference Templates

Reference implementations — use them directly, or fork to customize.

### Financial Morning Brief

For investors and analysts who read markets at 7am. **8 agents** — global market monitor, macro calendar, news filter, cross-asset reasoning, portfolio mapper, sector themes, charts, chief strategist. They answer *"what is the market trading today, and should I attack, defend, or watch?"* — not yet another news summary.

<!-- TODO: morning brief template demo video, ~30s -->

### Sales Agent Team

For solo founders and small sales teams. One instruction kicks off a sales team — you talk to just one agent, the rest of the team handles the back-office work: multi-channel outreach, sorting daily replies, updating customer state.

<!-- TODO: Sales team template demo video, ~30s -->

### One-click migration from OpenClaw / Hermes / Claude Code

Already using another AI tool? Two clicks bring your existing OpenClaw / Hermes / Claude Code agents over into a NarraNexus team — the setup and history you've already built come with them.

<!-- TODO: migration template demo video, ~30s -->

### More community templates → [browse all](https://narra.nexus/docs/modules/custom-modules)

> *All built by NarraNexus agents themselves.*

---

##  Honest Limitations

- **LLM API key**: the cloud version has a free trial quota. For daily or local use, you'll want your own LLM API key — one or two minutes to register at NetMind, OpenAI, or Anthropic.
- **An agent isn't 100% out of the box**: it needs your corrections and feedback to get sharp. Treat it like a new hire, not a god.
- **Collaboration isn't perfect on the first try**: complex tasks may take a couple of iterations before agents get the hang of it; some judgment calls always belong to you — the team handles the bulk, the key calls stay with you.
- **Architecture trade-off**: each agent launches its own MCP process, adding ~100ms of startup overhead. Negligible for chat-style workflows; switch to Direct Trigger mode for high-frequency jobs.

---

##  Community

<a name="community"></a>

- **Discord** — `coming soon`
- **Twitter / X** — `coming soon`
- **Email updates** — `coming soon`
- **Feedback** — [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

---

##  Contributing & governance

NarraNexus is built to work well with human and AI-agent contributors alike.

**Start here:**

- New contributors → [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- AI coding assistants → [`AGENTS.md`](./AGENTS.md) (vendor-neutral) or [`CLAUDE.md`](./CLAUDE.md) directly
- Project map for your AI agent → [`.mindflow/_overview.md`](./.mindflow/_overview.md)

**How the project is run:**

- Governance & maintainer team → [`GOVERNANCE.md`](./GOVERNANCE.md), [`MAINTAINERS.md`](./MAINTAINERS.md)
- Community standards → [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)
- Security policy → [`SECURITY.md`](./SECURITY.md)

See [`MAINTAINERS.md`](./MAINTAINERS.md) for the current maintainer team. Run `git shortlog -sn` for the full contributor list.

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
-->
