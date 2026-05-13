<!--
Draft: Structure A (Dev-focused) — 中文 — A3 hook
Date: 2026-05-12
Notes:
  - Audience: developers, tech-savvy evaluators, integrators
  - Tone: concept-based with some emotion (not pure tech)
  - 3 traits framed as "Capabilities" with dev language:
      ① 类人 Agent (Awareness + Narrative memory + MCP tools)
      ② 协作 (MessageBus protocol)
      ③ 开箱即用 (10 modules + 3 deploy paths + multi-LLM)
  - Hero: same template as B+ (AI Manga Explanation), placeholder = weather GIF
  - Templates: same 3 as B+ (synced)
  - 蹭势+差异化: 显式 callout single-agent paradigm (OpenClaw/ZeroClaw) in vs table
-->

<div align="center">

<img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

# 别只部署一个 Agent。直接组一支团队。

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Discord](https://img.shields.io/badge/Discord-Coming%20Soon-5865F2)](#社群)

[English](./README_A.en.md) | **中文**

</div>

---

<br/>

<p align="center">
  <em>懂记忆、懂协作、会用工具的 agent —— template 起步，也可以自己搭。</em>
</p>

<!-- TODO: replace showcase-weather.gif with PLACEHOLDER_hero_manga.gif once recorded -->
<p align="center">
  <img src="https://github.com/protagolabs/NarraNexus/raw/main/docs/images/showcase-weather.gif" alt="上传一章漫画，90 秒后拿到解说视频。" width="760" />
</p>

<p align="center">
  <em>上传一章漫画。<br/>
  90 秒后，一段 YouTube / Bilibili 风格的解说视频。<br/>
  <sub>(4 个 agent 接力：parse · narrate · score · render)</sub></em>
</p>

<p align="center">
  <a href="#templates">看更多 templates →</a>
</p>

<br/>

---

##  60 秒上手

NarraNexus 是一个多 Agent 产品 —— 不是给开发者搭 agent 的 framework，是直接给你一支可以协作的 agent 团队。每个 agent 持久身份、自带协作通道，三种部署任选。

云端注册、桌面下载、本地 build —— 选你顺手的一种。

### 🛠️ 本地从源码（推荐开发者）

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` 自动检测 `uv` / `node` / `tmux`，启动 7 个 service：

| Service | Port | 作用 |
|---------|------|------|
| Backend | 8000 | FastAPI server |
| DB Proxy | 8100 | SQLite HTTP proxy |
| MCP servers | 7801+ | Per-module tool servers |
| Frontend | 5173 | Vite dev server |
| Poller | — | Instance state monitor |
| Jobs Trigger | — | Scheduled job dispatcher |
| BusTrigger | — | Inter-agent message bus |

详细见 [开发文档](https://website.narra.nexus/docs/getting-started/quick-start)。

<!-- TODO: 本地 build demo video, ~30s -->
<p align="center">
  <em>📽️ 本地 build demo —— 待录制</em>
</p>

### 💻 macOS 桌面应用

1. [下载 .dmg](https://github.com/NetMindAI-Open/NarraNexus/releases/latest)
2. 拖入 Applications 文件夹
3. 启动 → 选一个 template，开始

<!-- TODO: dmg 安装 demo video, ~30s -->
<p align="center">
  <em>📽️ macOS 桌面安装 demo —— 待录制</em>
</p>

### ☁️ 云端注册

1. 打开 [agent.narra.nexus](https://agent.narra.nexus/login)
2. 注册账号
3. 选一个 template，开始

<!-- TODO: 云端注册 demo video, ~30s -->
<p align="center">
  <em>📽️ 云端注册 demo —— 待录制</em>
</p>

> 三种入口，殊途同归。

---

##  三大能力

按"agent 实际能做什么"组织。

### 类人的 Agent 员工

每个 agent 有持久身份（**Awareness module** 管理偏好、风格、约束），跨会话记得"它是谁、它在为谁工作"。对话被自动归到不同的 storyline —— **Narrative memory** 用 embedding 检索话题，不是按时间无脑排。每个 agent 都能调用 MCP 工具，装一个新 skill 一句话就能搞定，不用改代码。

### Agent 间真协作

不只是 chat —— agent 通过内置 **MessageBus** 协议直接对话：@mention、建房间、群聊。框架自带 rate limit 和 poison message 检测，防止 agent loop 失控。Agent 可以按能力被发现 —— 一个 agent 要找懂 SQL 的 helper，搜一下就有。

### Batteries included

**10 个内置模块**直接能用：Memory · Awareness · Chat · SocialNetwork · Jobs · Skills · MessageBus · Lark · CommonTools · BasicInfo。每个模块自带 DB schema、MCP tools 和生命周期 hook。**多 LLM** 支持（Anthropic / OpenAI / Gemini）通过统一适配层接入。**4 种 Trigger** 模式（Chat / Job / MessageBus / Matrix·Lark）共用同一个 6 步流水线。

---

<a name="templates"></a>

##  Reference Templates

参考实现 —— 直接套用，或 fork 一份自己改。

### 金融市场晨报

适合每天 7 点要看市场的投资者、研究员。**8 个 agent** —— 全球行情监测、宏观日历、新闻过滤、跨资产推理、持仓映射、行业主线、图表生成、首席策略师。回答的是 *"今天市场在交易什么？我该进攻、防守还是观望？"* —— 不是又一份新闻摘要。

<!-- TODO: 金融晨报 template demo video, ~30s -->
<p align="center">
  <em>📽️ 金融晨报演示 —— 待录制</em>
</p>

### Sales Agent Team

适合独立创业者和小型销售团队。一条指令直接启动一支 sales team —— 你只对接一个 agent，剩下交给团队自动办公：多渠道客户触达、整理每天的回复、更新客户状态。

<!-- TODO: Sales team template demo video, ~30s -->
<p align="center">
  <em>📽️ Sales Agent Team 演示 —— 待录制</em>
</p>

### 从 OpenClaw / Hermes / Claude Code 一键迁移

已经在用其他 AI 工具？两次点击把你的 OpenClaw / Hermes / Claude Code agent 搬过来，立刻在 NarraNexus 团队里用 —— 之前积累的设定一起带上。

<!-- TODO: 迁移 template demo video, ~30s -->
<p align="center">
  <em>📽️ 一键迁移演示 —— 待录制</em>
</p>

### 更多社区贡献的 template → [浏览全部](https://website.narra.nexus/docs/modules/custom-modules)

> *全部由 NarraNexus agent 自主完成。*

---

##  诚实边界

- **LLM API key**：线上版有免费额度可以试用。日常或本地用，需要你自己的 LLM API key —— 一两分钟在 NetMind / OpenAI / Anthropic 注册一个就够。
- **Agent 不是一上来就 100 分**：它需要你纠错、给反馈，越用越合手。把它当一个新员工，不是当神。
- **协作不是一次完美**：复杂任务往往要 agent 跑两三轮才上手；总有一些判断只能你来做 —— 团队跑通大部分，关键的判断留给你自己。
- **架构权衡**：每个 agent 启动自己的 MCP 进程，启动时大约多 100ms。chat-style workflow 不在意；高频 job 可以切到 Direct Trigger mode。

###  和其他工具的不同

| 你想… | 用… | 为什么 |
|-------|-----|--------|
| 用 Python 拼装 agent | LangChain · AutoGen · CrewAI | 库级别的零件，没有 opinionated runtime |
| 一个 personal assistant 跑遍 channel | OpenClaw · ZeroClaw · Claude Code | single-agent + 多 channel 范式 |
| **一支会协作、有记忆、能交付真正工作的 agent 团队** | **NarraNexus** | opinionated runtime + 热插拔模块 + 多 agent 协作 |

我们不替代上面那些工具。我们解决另一个问题：**不止 personal assistant，是一支 agent 团队。**

---

##  社群

<a name="社群"></a>

- **Discord** —— `即将上线`
- **Twitter / X** —— `即将上线`
- **邮件订阅** —— `即将上线`
- **反馈** —— [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)

---

##  如何贡献

### 我做了一个 template 想分享
打包成 `.nxbundle`，从下面任一渠道投递：

- 💬 **Discord** —— `即将上线`
- 📧 **邮件** —— `即将上线`
- 🐦 **Twitter / X DM** —— `即将上线`
- 🐛 **GitHub Issue** —— [开一个 issue](https://github.com/NetMindAI-Open/NarraNexus/issues)（带 `[template]` 前缀）

我们 review 后会放进官方 Templates 库。

### 我想写一个 Module
新 module 需要继承 `XYZBaseModule`、注册到 `MODULE_MAP`、补对应 schema 和 repository。详见 [新建 Module 指南](https://website.narra.nexus/docs/contributing/development-setup)。

### 我想改代码
- **Bug 反馈** → [GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)
- **新功能** → 先开 issue 讨论方向，再发 PR
- **Roadmap** → `即将上线`

---

## 许可证

[CC BY-NC 4.0](./LICENSE)

<!--
Alternative hooks (kept for record):

Current (A3):
  - "别只部署一个 agent。直接组一支团队。"  ← CURRENT (A3-zh)
  - "Don't deploy an agent. Launch a team."   ← A3-en counterpart

Other candidates considered:
  - A1: "一支 agent 团队，一键启动。"
  - A2: "一键启动你的 agent 团队。"
  - A4: "一键。一支 agent 团队。上线。"
  - A5: "不止 personal assistant —— 一键启动一支 agent 团队。"
  - A6: "一个多 Agent 产品，一键启动。"

合并版金句（备用，给老板挑）：
  "大多数 agent 工具是为开发者做的。NarraNexus 是为其他所有人做的。"

待补素材：
  - docs/images/PLACEHOLDER_hero_manga.gif (30 秒漫画解读 demo)
  - docs/images/PLACEHOLDER_install_local.gif (~30s 本地 build 流程)
  - docs/images/PLACEHOLDER_install_dmg.gif (~30s macOS dmg 安装流程)
  - docs/images/PLACEHOLDER_install_cloud.gif (~30s 云端注册流程)
  - docs/images/PLACEHOLDER_template_morning_brief.gif
  - docs/images/PLACEHOLDER_template_sales_team.gif
  - docs/images/PLACEHOLDER_template_openclaw_migrate.gif
-->
