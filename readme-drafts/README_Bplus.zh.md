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

<br/>

### NarraNexus 是一个桌面应用 —— 在这里你*雇佣*一个 AI 团队，而不是*搭建*一个。

挑一个 template，告诉它们你想要什么。它们自己沟通，记得上次做了什么，然后交付结果。

不用写代码。不用打开终端。不用 `pip install`。

<br/>

---

##  60 秒上手

| | |
|---|---|
| **macOS 桌面应用** | [下载 `.dmg` →](https://github.com/NetMindAI-Open/NarraNexus/releases) · 已签名公证、内置 Claude Code 登录 |
| **Web Demo (Beta)** | [在浏览器试用 →](https://website.narra.nexus/) · 不用安装 |
| **从源码安装**（开发者） | `git clone … && bash run.sh` · [详细文档](https://website.narra.nexus/docs/getting-started/quick-start) |

> 大部分人只需要 `.dmg`。双击。完成。

<!-- TODO: install flow GIF (download → double-click → see templates → pick one), ~15-20s. Slot reserved below. -->
<p align="center">
  <em>📽️ 安装到打开第一个 template 的 demo —— 待录制</em>
</p>

---

##  它是怎么工作的（不讲架构）

三件事让它不只是个 chatbot。

**1. 它会记得。**
不止上一条消息，是整段故事。上周五帮你写完周报的那个 agent，这周五能直接从上次的地方接着干。

**2. 它们会互相喊一声。**
写作 agent 缺一个事实，就去问研究员 agent。策略 agent 没把握，就升级给你。像一个小团队，知道什么时候该求助。

**3. 你用人话告诉它们。**
装技能靠聊天：*"从 ClawHub 装一个漫画 skill"*。Templates 两下点击拉起一整个团队。你只需要知道自己想做什么。

> 底层有 runtime、context engine、module system、MCP、hook manager 这些东西，但你永远不需要碰。想看的话 [文档在这](https://website.narra.nexus/docs/core-concepts/architecture)。

---

<a name="templates"></a>

##  Templates —— 真实团队，真实工作

5 个内置团队 template。上面 hero 是其中一个，下面是其余的。

###  金融市场晨报

<img src="docs/images/PLACEHOLDER_template_morning_brief.png" alt="" width="600" />

适合每天 7 点要看市场的投资者、研究员。**8 个 agent** —— 全球行情监测、宏观日历、新闻过滤、跨资产推理、持仓映射、行业主线、图表生成、首席策略师。它们回答的是 *"今天市场在交易什么？我该进攻、防守还是观望？"* —— 不是又一份新闻摘要。

###  Sales Agent Team

<img src="docs/images/PLACEHOLDER_template_sales_team.png" alt="" width="600" />

适合独立创业者和小型销售团队。**5 个 agent** 用一条指令启动多渠道触达 campaign。中间只跟 Master Agent 确认一次内容。每天自动 pull 客户回复，更新客户关系状态。

###  从 OpenClaw / Hermes / Claude Code 一键迁移

<img src="docs/images/PLACEHOLDER_template_openclaw_migrate.png" alt="" width="600" />

已经在用单 agent 工具？**一个引导式导入器**两次点击，把你现有的 OpenClaw / Hermes / Claude Code agent 和 skill，迁移成一个 NarraNexus 团队。你熟悉的 agent，现在会互相协作，还带着原来的 skill。

###  更多社区贡献的 template → [浏览全部](https://website.narra.nexus/docs/modules/custom-modules)

> *上面每个 template 都跑在 Hero demo 同一个引擎上，README 里没有任何造假。*

---

##  诚实边界

我们愿意提前告诉你。

- **你仍然需要一个 LLM API key。** NetMind.AI Power 一个 key 覆盖三个槽位（Agent / Embedding / Helper）。
- **目前 macOS 优先。** Windows / Linux 可从源码运行；原生安装包在路上。
- **部分模块仍是 experimental。** RAG 的 runtime 集成还在路上。EverMemOS（进阶情景记忆）默认关闭。
- **暂无移动端。**
- **Web Demo 是 beta。** 桌面端才是完整产品。

###  和其他工具的不同

| 你想… | 用… |
|------|----|
| 用 Python 自己搭 agent | LangChain · AutoGen · CrewAI |
| 一个 personal assistant 跑遍所有 channel | OpenClaw · ZeroClaw · Claude Code |
| **一个会协作、有记忆、能交付真正工作的 agent 团队，不写代码** | **NarraNexus** |

我们不替代上面那些工具。我们解决另一个问题：**搭 agent 不再是瓶颈了，雇 agent 才是。**

---

##  社群

<a name="社群"></a>

- **Discord** —— `即将上线`
- **Twitter / X** —— `即将上线`
- **邮件订阅** —— `即将上线`
- **文档** —— [website.narra.nexus](https://website.narra.nexus/docs)
- **反馈** —— [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

做出了对别人有用的 template？私信我们，放进 README。

---

## 致谢

NarraNexus 的可选长期记忆后端基于 **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)**。感谢 EverMemOS 团队。

> Chuanrui Hu et al. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[论文]](https://arxiv.org/abs/2601.02163)

## 引用

```bibtex
@software{narranexus2026,
  title  = {NarraNexus: A Framework for Building Nexuses of Agents},
  author = {NetMind.AI},
  year   = {2026},
  url    = {https://github.com/NetMindAI-Open/NarraNexus},
  license = {CC-BY-NC-4.0}
}
```

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
