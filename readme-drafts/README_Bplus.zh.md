<!--
Draft: Structure B+ (fresh user focused) — 中文 — Emotion-driven hook
Date: 2026-05-12
Notes:
  - 前三屏完全没有任何技术解释
  - Hero demo: AI 漫画解读 (Hongyi Gu) — 单个 30 秒 GIF
  - Templates 仍在 #7（按用户决定）；hero 区给一个 "看更多 →" 链到 #7
  - Templates: 金融市场晨报 (Bin Liang) / OpenClaw 迁移 (Jiaxi Chen) / Sales Agent Team (Jiaxi Chen)
  - 合并版金句 at bottom commented
-->

<div align="center">

<img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

# 你出想法，AI 团队出结果。

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#社群)

[English](./README_Bplus.en.md) | **中文**

</div>

---

<br/>

<!-- TODO: replace showcase-weather.gif placeholder with PLACEHOLDER_hero_manga.gif (manga input → narrated output, ~30s) once recorded -->
<p align="center">
  <img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/images/showcase-weather.gif" alt="上传一章漫画，90 秒后发出去。" width="760" />
</p>

<p align="center">
  <em>上传一章漫画。<br/>
  90 秒后，你拿到一段带配音、特效、解说的视频。<br/>
  发出去就行。</em>
</p>

<p align="center">
  <a href="#templates">看更多 templates →</a>
</p>

<br/>

---

##  60 秒上手

NarraNexus 让你直接用一个 AI 团队 —— 或者从零搭一个自己的。
现成 template 几秒上手；自建，也只是几步的事。

桌面下载、云端注册、本地 build —— 选你顺手的一种：

| | |
|---|---|
| **macOS 桌面应用** | [下载 `.dmg` →](https://github.com/NetMindAI-Open/NarraNexus/releases) · 双击，直接对话 |
| **云端注册** | [打开 →](https://website.narra.nexus/) · 注册即用 |
| **本地 build**（开发者） | `git clone … && bash run.sh` · [详细文档](https://website.narra.nexus/docs/getting-started/quick-start) |

> 三种入口，殊途同归。

<!-- TODO: install flow GIF, ~15-20s -->
<p align="center">
  <em>📽️ 从零到第一个 template 的 demo —— 待录制</em>
</p>

---

##  它是怎么工作的（不讲架构）

三件事让它不只是个 chatbot。

**1. 它会记得。**
不止上一条消息，是整段故事。上周五帮你写完周报的那个 agent，这周五能直接从上次的地方接着干。

**2. 它们自己分工。**
一件事需要研究 + 写作 + 出图？它们接力做完，不用你来拆任务。结果交付给你时，已经组装好了。

**3. 它们会站在别人肩上。**
不会做的事？复用一个别人验证过的 template。缺一项能力？让 agent 自己去学，不用你教。

> 底层有 runtime、context engine、module system、MCP、hook manager 这些东西，但你永远不需要碰。想看的话 [文档在这](https://website.narra.nexus/docs/core-concepts/architecture)。

---

<a name="templates"></a>

##  Templates —— 真实团队，真实工作

上面 Hero 是其中一个。下面看其他的。

###  金融市场晨报

<img src="docs/images/PLACEHOLDER_template_morning_brief.png" alt="" width="600" />

适合每天 7 点要看市场的投资者、研究员。**8 个 agent** —— 全球行情监测、宏观日历、新闻过滤、跨资产推理、持仓映射、行业主线、图表生成、首席策略师。它们回答的是 *"今天市场在交易什么？我该进攻、防守还是观望？"* —— 不是又一份新闻摘要。

###  Sales Agent Team

<img src="docs/images/PLACEHOLDER_template_sales_team.png" alt="" width="600" />

适合独立创业者和小型销售团队。一条指令直接启动一支 sales team —— 你只对接一个 agent，剩下交给团队自动办公：多渠道客户触达、整理每天的回复、更新客户状态。

###  从 OpenClaw / Hermes / Claude Code 一键迁移

<img src="docs/images/PLACEHOLDER_template_openclaw_migrate.png" alt="" width="600" />

已经在用其他 AI 工具？两次点击把你的 OpenClaw / Hermes / Claude Code agent 搬过来，立刻在 NarraNexus 团队里用 —— 之前积累的设定一起带上。

###  更多社区贡献的 template → [浏览全部](https://website.narra.nexus/docs/modules/custom-modules)

> *上面每个 template 都跑在 Hero demo 同一个引擎上，README 里没有任何造假。*

---

##  诚实边界

我们愿意提前告诉你。

- **LLM API key**：线上版有免费额度可以试用。日常或本地用，需要你自己的 LLM API key —— 一两分钟在 NetMind / OpenAI / Anthropic 注册一个就够。
- **Agent 不是一上来就 100 分**：它需要你纠错、给反馈，越用越合手。把它当一个新员工，不是当神。
- **协作不是一次完美**：复杂任务往往要 agent 跑两三轮才上手；总有一些判断只能你来做 —— 团队跑通大部分，关键的判断留给你自己。

###  和其他工具的不同

| 你想… | 用… |
|------|----|
| 用 Python 自己搭 agent | LangChain · AutoGen · CrewAI |
| 一个 personal assistant 跑遍所有 channel | OpenClaw · ZeroClaw · Claude Code |
| **一个会协作、有记忆、能交付真正工作的 agent 团队，不写代码** | **NarraNexus** |

我们不替代上面那些工具。我们解决另一个问题：**搭 agent 不再是瓶颈了。把 agent 团队真的用起来，才是。**

---

##  社群

<a name="社群"></a>

- **Discord** —— `即将上线`
- **Twitter / X** —— `即将上线`
- **邮件订阅** —— `即将上线`
- **反馈** —— [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

---

##  如何贡献

### 我是用户，做了一个想分享的 template
做出了对别人有用的 agent 团队？打包成 `.nxbundle`，从下面任一渠道投递：

- 💬 **Discord** —— `即将上线`
- 📧 **邮件** —— `即将上线`
- 🐦 **Twitter / X DM** —— `即将上线`
- 🐛 **GitHub Issue** —— [开一个 issue](https://github.com/NetMindAI-Open/NarraNexus/issues)（带 `[template]` 前缀）

我们 review 后会放进官方 Templates 库。

### 我是开发者，想改代码
- **Bug 反馈** → [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)
- **新功能** → 先开 issue 讨论方向，再发 PR
- **新 module** → 见 [开发指南](https://website.narra.nexus/docs/contributing/development-setup)
- **Roadmap** → `即将上线`

---

## 许可证

[CC BY-NC 4.0](./LICENSE)

<!--
Alternative hooks (kept for record, in case we want to swap):

Direction A · 双段式 (clean division):
  - "你出想法，AI 团队出结果。"  ← CURRENT (B2)
  - "你来想，它们来做。"  (B3, terser)
  - "你的想法。AI 团队的执行。"

Direction B · 改"想做的事"为"想法":
  - "你的想法，让 AI 团队替你做完。"  (B1, minimal edit of original option c)
  - "想到的事，让 AI 团队替你跑完。"  (B4)

Direction C · 花叔三段式 (action-action-result):
  - "想清楚一件事。说一句话。AI 团队交付。"  (B5)
  - "你的想法，几句话，AI 团队替你做出来。"  (B6, explicit "几句话")

合并版金句（备用，给老板挑）：
  "大多数 agent 工具是为开发者做的。NarraNexus 是为其他所有人做的。"

待补素材：
  - docs/images/PLACEHOLDER_hero_manga.gif (30 秒漫画解读 demo — 整个 README 最关键的视觉资产)
  - docs/images/PLACEHOLDER_install_flow.gif (15-20 秒安装到打开第一个 template，放 Quick Start 区)
  - docs/images/PLACEHOLDER_template_morning_brief.png
  - docs/images/PLACEHOLDER_template_sales_team.png
  - docs/images/PLACEHOLDER_template_openclaw_migrate.png
-->
