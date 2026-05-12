<!--
Draft: Structure A (General) — English — Problem-driven hook
Date: 2026-05-12
Notes:
  - Hero demo: AI Manga Explanation (Hongyi Gu)
  - Templates: Financial Morning Report (Bin Liang) / OpenClaw migration (Jiaxi Chen) / Sales Agent Team (Jiaxi Chen)
  - Placeholders: docs/images/PLACEHOLDER_*.gif / .png
  - "Merged hook" alternative is at the bottom commented out for boss option
-->

<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

### Hiring an AI team shouldn't require reading ten docs.

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#community)

**English** | [中文](./README_A.zh.md)

</div>

---

## The problem

Most AI tools today still want you to be a developer. Frameworks like LangChain, AutoGen, and CrewAI are powerful — if you can write Python and orchestrate state machines. Personal assistants like OpenClaw, ZeroClaw, and Claude Code are great — if one assistant on many channels is what you actually need.

But what if you want **a team of agents** that collaborate, remember each other's work, and ship something real — and you'd rather not open a terminal?

## NarraNexus

NarraNexus is a desktop app that lets anyone hire a team of AI agents the same way you'd hire teammates: pick a template, give them goals, let them talk to each other.

- **No code required.** Install the `.dmg`, click, done.
- **Templates that ship real work**, not toy demos.
- **Agents that remember and collaborate** across days, sessions, and channels.

<br/>

<p align="center">
  <img src="docs/images/PLACEHOLDER_hero_manga.gif" alt="AI Manga Explanation demo" />
</p>
<p align="center"><em>Upload a manga page → the agent team explains it as a narrated manga with effects.</em></p>

> Every template in this README runs on the same engine. Nothing is faked.

---

## Quick Start

Pick one. All three reach the same product.

###  Desktop App — recommended for everyone

> **[Download Latest Release →](https://github.com/NetMindAI-Open/NarraNexus/releases)** — choose the file ending with `.dmg`. Signed, notarized, includes in-app Claude Code login. No terminal needed.

###  Web Demo (Beta)

> **[Launch Web Demo →](https://website.narra.nexus/)** — try in your browser, no install.

###  From source (developers)

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` handles dependencies, env, and LLM provider setup. Detailed setup in [the docs](https://website.narra.nexus/docs/getting-started/quick-start).

---

## Feature highlights

Outcome-framed, not capability-framed.

| What you get | What it means |
|--------------|---------------|
| **A team you hire, not build** | Pick a template; agents are pre-wired with roles, memory, and tools |
| **Agents that remember** | Every conversation is routed into a topic-aware storyline that persists across sessions |
| **Agents that talk to each other** | Built-in messaging bus — agents @-mention each other, hand off tasks, escalate on stuck |
| **Skills installed by chat** | Tell an agent *"install the manga skill from ClawHub"* — it does |
| **Voice, image, and rich output** | Voice in, transcribed; image attachments; agents render HTML, PDF, charts, slides |
| **One-click sharing** | `.nxbundle` export packages an agent team for a colleague to import |
| **Local-first** | Your data, your machine. Cloud is optional. |
| **Multi-LLM** | Claude, OpenAI, Gemini — swap any time |

> Most multi-agent frameworks have similar features under the hood. NarraNexus is different in **who it's for**: not developers building agents, but anyone who wants to hire one.

---

## Templates / Use cases

Five team templates ship in the box. Each one is a real working scenario, not a demo.

###  Financial Morning Brief

*For:* investors, analysts, anyone who reads markets at 7am.
*Team:* 8 agents — global market monitor, macro calendar, news filter, cross-asset reasoning, portfolio mapper, sector themes, chart generator, chief strategist.
*Output:* a daily decision-ready brief that answers *"what is the market trading today, and should I attack, defend, or watch?"* — not a news summary.

###  AI Manga Explanation

*For:* creators, educators, anyone who wants to turn a page of manga into a narrated explanation.
*Team:* upload-parser → frame-narrator → effect-composer → renderer.
*Output:* a shareable manga walkthrough with voice, effects, and per-frame commentary. (This is the hero demo above.)

###  Sales Agent Team

*For:* solo founders, small sales teams.
*Team:* 5 agents — content writer, outreach drafter, reply auto-responder, CRM updater, manager.
*Output:* one instruction kicks off a multi-channel outreach campaign. You confirm content once. Daily replies are pulled and customer state is updated automatically.

###  Migrate from OpenClaw / Hermes / Claude Code

*For:* people already using single-agent tools who want a team.
*Team:* a guided importer that turns your existing OpenClaw / Hermes / Claude Code agents and skills into a NarraNexus team in two clicks.
*Output:* your familiar agents, now collaborating — and bringing their skills with them.

###  (More templates from the community — submit yours)

Templates are first-class citizens. To add your own, see [Custom Templates](https://website.narra.nexus/docs/modules/custom-modules).

---

## Honest limitations

Things we'd rather tell you up-front.

- **You still need an LLM API key.** NetMind.AI Power covers all three slots (Agent / Embedding / Helper) with one key — easiest path.
- **Currently macOS-first.** Windows/Linux work from source; native installers in progress.
- **Some modules are experimental.** RAG file management API is live; runtime RAG module integration is still in progress. EverMemOS (advanced episodic memory) is opt-in.
- **No mobile app yet.**
- **Web Demo is beta.** Latency and limits apply; the desktop app is the real product.

### How we're different (and how we're not)

We don't replace what already works for developers. We solve a different problem.

| If you want… | Use… |
|--------------|------|
| To build agents in Python | LangChain, AutoGen, CrewAI — they're great |
| One personal assistant across all your channels | OpenClaw, ZeroClaw, Claude Code — they're great |
| **A team of agents that collaborate, remember, and ship work — without writing code** | **NarraNexus** |

---

## Community

<a name="community"></a>

- **Discord** — `coming soon` *(invite link will land here once live)*
- **Twitter / X** — `coming soon`
- **Email updates** — `coming soon` *(we want one before stars become anonymous numbers)*
- **Docs** — [website.narra.nexus](https://website.narra.nexus/docs)
- **Issues / feedback** — [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

> If you've shipped a NarraNexus template that helps people, DM us — we'll feature it.

---

## Acknowledgments

NarraNexus's optional long-term memory backend is built on **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)**. We thank the EverMemOS team for their work.

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
  - docs/images/PLACEHOLDER_hero_manga.gif (30s manga explanation demo)
  - docs/images/PLACEHOLDER_template_morning_brief.png
  - docs/images/PLACEHOLDER_template_sales_team.png
  - docs/images/PLACEHOLDER_template_openclaw_migrate.png
-->
