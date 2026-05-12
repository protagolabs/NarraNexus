<!--
Draft: Structure A (General) — 中文 — Problem-driven hook
Date: 2026-05-12
Notes:
  - Hero demo: AI Manga Explanation (Hongyi Gu)
  - Templates: 金融市场晨报 (Bin Liang) / OpenClaw 迁移 (Jiaxi Chen) / Sales Agent Team (Jiaxi Chen)
  - Placeholders: docs/images/PLACEHOLDER_*.gif / .png
  - 合并版金句 at bottom commented for boss option
-->

<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

### 雇一个 AI 团队，不应该要先读十份文档。

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#社群)

[English](./README_A.en.md) | **中文**

</div>

---

## 痛点

现在大多数 AI 工具仍然假定你是开发者。LangChain / AutoGen / CrewAI 很强 —— 前提是你会写 Python、能搭 state machine。OpenClaw / ZeroClaw / Claude Code 也很好 —— 前提是"一个 personal assistant 在多个 channel 跑"就是你要的。

但如果你想要**一个 agent 团队** —— 它们会互相协作、记得彼此的工作、一起交付一件真实的事 —— 又不想打开 terminal，怎么办？

## NarraNexus

NarraNexus 是一个桌面应用，让任何人都能像招同事一样雇佣一支 AI 团队：挑一个 template，给它们目标，让它们自己沟通。

- **不需要写代码。** 装 `.dmg`，双击，开始。
- **Templates 直接交付真实工作**，不是 toy demo。
- **Agent 会记忆、会协作**，跨天、跨会话、跨渠道。

<br/>

<p align="center">
  <img src="docs/images/PLACEHOLDER_hero_manga.gif" alt="AI 漫画解读 demo" />
</p>
<p align="center"><em>上传漫画 → agent 团队把它讲成一个带配音和特效的漫画解读视频。</em></p>

> README 里展示的每一个 template 都跑在同一个引擎上，没有任何造假。

---

## 快速开始

三选一，殊途同归。

###  桌面 App —— 推荐给所有人

> **[下载最新版本 →](https://github.com/NetMindAI-Open/NarraNexus/releases)** —— 选 `.dmg` 文件。已签名、已公证，内置 Claude Code 登录，不用打开终端。

###  Web Demo (Beta)

> **[在浏览器中试用 →](https://website.narra.nexus/)** —— 不用安装。

###  从源码安装（开发者）

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` 自动处理依赖、环境、LLM 配置。详细见[文档](https://website.narra.nexus/docs/getting-started/quick-start)。

---

## 核心特性

按"你得到什么"组织，不是按"我有什么 feature"组织。

| 你得到的 | 它的意思是 |
|---------|-----------|
| **雇一个团队，不是搭一个团队** | 选一个 template，agents 自带角色、记忆和工具 |
| **会记忆的 agent** | 每段对话都被路由到一条 topic-aware 故事线，跨会话延续 |
| **会互相喊一声的 agent** | 内置消息总线 —— agent 之间 @ 提及、交接任务、卡住时升级 |
| **聊天就能装技能** | 跟 agent 说"从 ClawHub 装一个漫画 skill"，它就去装 |
| **语音、图片、富文本输出** | 语音输入实时转写；图片附件；agent 能直接生成 HTML、PDF、图表、PPT |
| **一键分享** | `.nxbundle` 打包整个 agent 团队，同事导入即用 |
| **本地优先** | 数据在你机器上。云端只是可选。 |
| **多 LLM** | Claude、OpenAI、Gemini 随时切换 |

> 大多数 multi-agent framework 底层 feature 都差不多。NarraNexus 真正不同的，是**它是为谁做的**：不是给开发者搭 agent，是给任何想雇一个 agent 团队的人。

---

## Templates / 使用场景

5 个内置团队 template，每一个都是真实工作场景，不是 demo。

###  金融市场晨报

*适合：* 投资者、研究员、每天 7 点想看一份"能用"的晨报的人。
*团队：* 8 个 agent —— 全球行情监测、宏观日历、新闻过滤、跨资产推理、持仓映射、行业主线、图表生成、首席策略师。
*产出：* 一份 *可决策* 的晨报，回答"今天市场在交易什么？该进攻、防守，还是观望？" —— 不是新闻摘要。

###  AI 漫画解读

*适合：* 内容创作者、教育工作者、想把一页漫画变成讲解视频的人。
*团队：* 上传解析 → 分镜叙述 → 特效合成 → 渲染输出。
*产出：* 一个带配音、特效、逐格解读的可分享视频。（上面 hero demo 就是它。）

###  Sales Agent Team

*适合：* 独立创业者、小型销售团队。
*团队：* 5 个 agent —— 内容生成、外发草拟、自动回复、CRM 更新、Master 经理。
*产出：* 一句话指令 → 一个多渠道触达 campaign。中间只跟 Master Agent 确认一次内容。每天自动 pull 客户回复，更新客户关系状态。

###  从 OpenClaw / Hermes / Claude Code 一键迁移

*适合：* 已经在用单 agent 工具、想升级到团队的人。
*团队：* 一个引导式导入器，把你现有的 OpenClaw / Hermes / Claude Code agent 和 skill，两次点击迁移成 NarraNexus 团队。
*产出：* 你熟悉的 agent，现在会互相协作，还带着原来的 skill。

###  （更多 template，社区贡献）

Template 是一等公民。要做自己的 template，见 [Custom Templates](https://website.narra.nexus/docs/modules/custom-modules)。

---

## 诚实边界

我们愿意提前告诉你。

- **你仍然需要一个 LLM API key。** NetMind.AI Power 一个 key 覆盖三个槽位（Agent / Embedding / Helper），最快路径。
- **目前 macOS 优先。** Windows / Linux 可从源码运行，原生安装包还在路上。
- **部分模块仍是 experimental。** RAG 的文件管理 API 已经可用，runtime 模块集成还在路上。EverMemOS（进阶情景记忆）默认关闭。
- **暂无移动端。**
- **Web Demo 是 beta。** 延迟和限额都存在；桌面端才是完整产品。

### 和其他工具的不同

我们不替代已经能解决问题的工具，我们解决另一个问题。

| 如果你想… | 用… |
|---------|----|
| 用 Python 自己搭 agent | LangChain / AutoGen / CrewAI —— 它们很专业 |
| 一个 personal assistant 跑遍你所有 channel | OpenClaw / ZeroClaw / Claude Code —— 它们很棒 |
| **一个会协作、有记忆、能交付真正工作的 agent 团队，不写代码** | **NarraNexus** |

---

## 社群

<a name="社群"></a>

- **Discord** —— `即将上线` *(链接占位)*
- **Twitter / X** —— `即将上线`
- **邮件订阅** —— `即将上线` *(在 star 变成匿名数字之前，我们想认识你)*
- **文档** —— [website.narra.nexus](https://website.narra.nexus/docs)
- **反馈 / Bug** —— [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

> 如果你做出了一个 NarraNexus 上对别人有用的 template，私信我们 —— 我们想把它放进 README。

---

## 致谢

NarraNexus 的可选长期记忆后端基于 **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)**。感谢 EverMemOS 团队的工作。

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
合并版金句（备用，给老板挑）：
  "大多数 agent 工具是为开发者做的。NarraNexus 是为其他所有人做的。"

待补素材：
  - docs/images/PLACEHOLDER_hero_manga.gif (30 秒漫画解读 demo)
  - docs/images/PLACEHOLDER_template_morning_brief.png
  - docs/images/PLACEHOLDER_template_sales_team.png
  - docs/images/PLACEHOLDER_template_openclaw_migrate.png
-->
