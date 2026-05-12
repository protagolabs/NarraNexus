<div align="center">

# 🚧 README Drafts — Review Branch

**This is a discussion branch. Do not merge.**

The real project README lives on [`main`](https://github.com/protagolabs/NarraNexus/blob/main/README.md). This branch holds four candidate rewrites for review based on early-stage product goals (10-second value proposition, 3–5 templates as concrete handles, growth toward non-coding users).

</div>

---

## Why this branch exists

Boss feedback on the previous rewrite (`readme_rewrite_2025_05_10`, the v1.4.1 alignment one): it doesn't speak to the early-stage goals.

> Target audience: **小白用户 that doesn't even know CLI.** Goals: (1) 10s product cognition, (2) 3–5 templates as concrete handles, (3) early growth via stars / community / KOL / feedback.

Boss also flagged that the feature list isn't differentiated — every multi-agent framework has similar features. We can't change the features, but we can change **who the README is for** and **how it positions against the landscape**.

These four drafts are different bets on how to do that.

---

## The four candidates

All four share the **same content schema** (positioning, templates, honest limitations, community block). They differ on **structure** and **金句 / hook tone**.

| File | Structure | Language | Hook tone | 一句话 pitch |
|------|-----------|----------|-----------|--------------|
| [`readme-drafts/README_A.en.md`](./readme-drafts/README_A.en.md) | A · General | English | Problem-driven | *"Hiring an AI team shouldn't require reading ten docs."* |
| [`readme-drafts/README_A.zh.md`](./readme-drafts/README_A.zh.md) | A · General | 中文 | Problem-driven | *"雇一个 AI 团队，不应该要先读十份文档。"* |
| [`readme-drafts/README_Bplus.en.md`](./readme-drafts/README_Bplus.en.md) | B+ · Fresh user focused | English | Emotion-driven | *"Your next teammate doesn't have to be human."* |
| [`readme-drafts/README_Bplus.zh.md`](./readme-drafts/README_Bplus.zh.md) | B+ · Fresh user focused | 中文 | Emotion-driven | *"你的下一个团队成员，不一定是人类。"* |

Each file also carries a **merged hook** in the trailing HTML comment, as an alternative for the boss to pick:

- EN: *"Most agent tools are built for developers. NarraNexus is built for everyone else."*
- ZH: *"大多数 agent 工具是为开发者做的。NarraNexus 是为其他所有人做的。"*

---

## Structure cheat sheet

### Structure A (General)
```
1. Logo + badges
2. The problem  ← problem-driven hook lives here
3. NarraNexus (one-line solution)
4. Hero demo (GIF)
5. Quick Start (DMG → Web → Source)
6. Feature highlights (outcome-framed)
7. Templates / use cases
8. Honest limitations + how we compare
9. Community
```

### Structure B+ (Fresh user focused)
```
1. Logo + badges
2. 金句 hook  ← emotion-driven, no technical words for first three screens
3. Hero demo (GIF) + "see more templates →" link to section 7
4. One-line "what is this"
5. Quick Start (DMG → Web → Source, compressed)
6. How it works (story, not architecture)
7. Templates / use cases
8. Honest limitations + how we compare
9. Community
```

**Templates section is identical between A and B+**: AI Manga Explanation is the Hero; Financial Morning Brief, Sales Agent Team, and OpenClaw·Hermes·Claude Code Migration ship below.

---

## What's the same in all four

- **Hero demo**: AI Manga Explanation (upload manga → narrated walkthrough video). Hongyi Gu's template.
- **Templates listed**: Financial Morning Brief (Bin Liang) · AI Manga Explanation (Hongyi Gu) · Sales Agent Team (Jiaxi Chen) · Migration from OpenClaw/Hermes/Claude Code (Jiaxi Chen).
- **Positioning table** (vs LangChain/AutoGen/CrewAI/OpenClaw/ZeroClaw/Claude Code):
  - LangChain/AutoGen/CrewAI → *for developers building agents*
  - OpenClaw/ZeroClaw/Claude Code → *one personal assistant across channels*
  - **NarraNexus → a team of agents that collaborate, remember, and ship work, for people who don't write code**
- **Honest limitations**: still need LLM API key (NetMind.AI Power one-key path), macOS-first, RAG experimental, no mobile, Web Demo is beta.
- **Community placeholders**: Discord / Twitter / Email all marked `coming soon`.

---

## What's different between A and B+

| Dimension | A (General) | B+ (Fresh user focused) |
|-----------|-------------|-------------------------|
| **First impression** | Names a pain explicitly, then offers a solution | An emotional line + a single hero GIF, zero technical words for ~3 screens |
| **Hook intent** | "I have a problem this might fix" | "I want one of these" |
| **Architecture / how-it-works** | Bullet-style feature highlights (outcome-framed) | Three short story paragraphs (remember / talk to each other / plain words) |
| **Best for** | Boss / investor / dev reviewer who wants to know *what it is* fast | Twitter share / KOL retweet / non-technical user who clicks from social |
| **Length** | Slightly longer (199 lines) | Slightly shorter (189 lines) |

---

## Open questions for review

Things worth deciding while reading:

1. **Hook tone** — Problem (A) or emotion (B+) feels more on-brand? Or is the merged version (in the HTML comments at the bottom of each file) the strongest?
2. **Hero demo asset** — AI Manga Explanation is the proposed Hero. Is that the right one or should it be Financial Morning Brief / Sales Team?
3. **Templates lineup** — three templates ship below Hero (Morning Brief / Sales / OpenClaw Migrate). Add a fourth? Cut one? Reorder?
4. **Positioning table** — does the LangChain/OpenClaw/NarraNexus three-way split land, or does it need sharpening?
5. **EN vs ZH** — same hook in both, or different hooks calibrated to each market?
6. **What lands first on the GitHub home page (post-merge)** — A or B+?

---

## Placeholder assets still needed

The drafts reference these. None exist yet; they're tagged `PLACEHOLDER_*` so we can grep when ready to ship.

- `docs/images/PLACEHOLDER_hero_manga.gif` ← **most important**, 30-second AI Manga Explanation demo
- `docs/images/PLACEHOLDER_template_morning_brief.png`
- `docs/images/PLACEHOLDER_template_sales_team.png`
- `docs/images/PLACEHOLDER_template_openclaw_migrate.png`

---

## How to leave feedback

- **Inline**: click a draft file, click line numbers to permalink, share the URL — fastest for boss / colleague comments
- **GitHub issue**: open one on this repo and reference this branch
- **Direct**: ping Hongyi Gu

---

## Once we pick a winner

1. Spawn a **preview branch** (`readme-preview-A` or `readme-preview-Bplus`) where root `README.md` is replaced with the chosen draft — that simulates what the actual repo home page will look like.
2. Capture / record the four placeholder assets.
3. Merge into the existing `readme_rewrite_2025_05_10` PR (or open a fresh PR off `main`, depending on what's cleaner).
4. Delete this drafts branch.

---

*Drafts created 2026-05-12. Discussion log: `drafts/logs/readme_repositioning_2026_05_12.md` (local-only).*
