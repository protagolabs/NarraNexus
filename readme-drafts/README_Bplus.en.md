<!--
Draft: Structure B+ (fresh user focused) — English — Emotion-driven hook
Date: 2026-05-12
Notes:
  - First three screens = zero technical explanation
  - Hero demo: AI Manga Explanation (Hongyi Gu) — single 30s GIF
  - Templates still at section 7 (per user); hero block has "more →" link to #7
  - Templates: 金融市场晨报 (Bin Liang) / OpenClaw 迁移 (Jiaxi Chen) / Sales Agent Team (Jiaxi Chen)
  - Merged hook alternative at bottom commented
-->

<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

# Your next teammate doesn't have to be human.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#community)

**English** | [中文](./README_Bplus.zh.md)

</div>

---

<br/>

<p align="center">
  <img src="docs/images/PLACEHOLDER_hero_manga.gif" alt="Upload a manga page. Get back a narrated, effects-laden walkthrough." width="760" />
</p>

<p align="center">
  <em>Upload one manga page.<br/>
  A team of four agents reads it, narrates it, scores it, animates it.<br/>
  You get a shareable video in 90 seconds.</em>
</p>

<p align="center">
  <a href="#templates">See more templates →</a>
</p>

<br/>

---

<br/>

### NarraNexus is a desktop app where you *hire* a team of AI agents instead of building one.

Pick a template. Tell them what you want. They talk to each other, remember what they did last time, and ship the result.

No code. No CLI. No `pip install`.

<br/>

---

##  Try it in 60 seconds

| | |
|---|---|
| **macOS desktop app** | [Download `.dmg` →](https://github.com/NetMindAI-Open/NarraNexus/releases) · signed, notarized, in-app Claude Code login |
| **Web Demo (Beta)** | [Open in browser →](https://website.narra.nexus/) · no install |
| **From source** (devs) | `git clone … && bash run.sh` · [setup docs](https://website.narra.nexus/docs/getting-started/quick-start) |

> Most people want the `.dmg`. Double-click. Done.

---

##  How it works (without the architecture talk)

Three things make this different from a chatbot.

**1. Your agents remember.**
Not just the last message. The whole story. The agent who summarized your week last Friday will pick up where it left off this Friday.

**2. Your agents talk to each other.**
When the writer-agent needs a fact, it pings the researcher-agent. When the strategist-agent isn't sure, it escalates to you. Like a small team that knows when to ask for help.

**3. You tell them in plain words.**
Skills install by chat: *"install the manga skill from ClawHub."* Templates spin up a whole team in two clicks. The only thing you have to know is what you want done.

> Under the hood there's a runtime, a context engine, a module system, MCP, a hook manager. You'll never touch any of it. [Read the docs](https://website.narra.nexus/docs/core-concepts/architecture) if you want to.

---

<a name="templates"></a>

##  Templates — real teams, real work

Five team templates ship in the box. The Hero above is one of them. Here are the rest.

###  Financial Morning Brief

<img src="docs/images/PLACEHOLDER_template_morning_brief.png" alt="" width="600" />

For investors who read markets at 7am. **8 agents** — global market monitor, macro calendar, news filter, cross-asset reasoning, portfolio mapper, sector themes, charts, chief strategist. They answer *"what is the market trading today, and should I attack, defend, or watch?"* — not yet another news summary.

###  Sales Agent Team

<img src="docs/images/PLACEHOLDER_template_sales_team.png" alt="" width="600" />

For solo founders and small sales teams. **5 agents** kick off a multi-channel outreach campaign from one instruction. You confirm content once with the manager agent. Daily replies are pulled and customer state is updated automatically.

###  Migrate from OpenClaw / Hermes / Claude Code

<img src="docs/images/PLACEHOLDER_template_openclaw_migrate.png" alt="" width="600" />

Already using a single-agent tool? **A guided importer** turns your existing OpenClaw / Hermes / Claude Code agents and skills into a NarraNexus team in two clicks. Your familiar agents, now collaborating — and bringing their skills with them.

###  More templates from the community → [browse all](https://website.narra.nexus/docs/modules/custom-modules)

> *Every template above runs on the same engine the Hero demo runs on. Nothing in this README is mocked up.*

---

##  Honest limitations

A few things we'd rather tell you up-front.

- **You still need an LLM API key.** NetMind.AI Power covers all three slots (Agent / Embedding / Helper) with one key.
- **macOS-first today.** Windows / Linux work from source; native installers in progress.
- **Some modules are experimental.** RAG runtime integration is in progress. EverMemOS (advanced episodic memory) is opt-in.
- **No mobile app yet.**
- **Web Demo is beta.** The desktop app is the real product.

###  How we compare

| If you want… | Use… |
|--------------|------|
| To build agents in Python | LangChain · AutoGen · CrewAI |
| One personal assistant across your channels | OpenClaw · ZeroClaw · Claude Code |
| **A team of agents that collaborate, remember, and ship work — without writing code** | **NarraNexus** |

We don't replace those tools. We solve a different problem: **building agents is no longer the bottleneck — hiring them is.**

---

##  Community

<a name="community"></a>

- **Discord** — `coming soon`
- **Twitter / X** — `coming soon`
- **Email updates** — `coming soon`
- **Docs** — [website.narra.nexus](https://website.narra.nexus/docs)
- **Feedback** — [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

If you ship a template that helps people, DM us — we'll feature it.

---

## Acknowledgments

NarraNexus's optional long-term memory backend is built on **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)**. We thank the EverMemOS team.

> Chuanrui Hu et al. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[Paper]](https://arxiv.org/abs/2601.02163)

## Citation

```bibtex
@software{narranexus2026,
  title  = {NarraNexus: A Framework for Building Nexuses of Agents},
  author = {NetMind.AI},
  year   = {2026},
  url    = {https://github.com/NetMindAI-Open/NarraNexus},
  license = {CC-BY-NC-4.0}
}
```

## License

[CC BY-NC 4.0](./LICENSE)

<!--
ALTERNATIVE HOOK (merged, for boss's review):
  "Most agent tools are built for developers. NarraNexus is built for everyone else."

PLACEHOLDER ASSETS NEEDED:
  - docs/images/PLACEHOLDER_hero_manga.gif (30s manga explanation — THE single most important visual asset)
  - docs/images/PLACEHOLDER_template_morning_brief.png
  - docs/images/PLACEHOLDER_template_sales_team.png
  - docs/images/PLACEHOLDER_template_openclaw_migrate.png
-->
