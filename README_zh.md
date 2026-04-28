<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

**构建 Agent 之间的「连接网络」-- 让智能从交互中涌现，而非孤立运行。**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://www.narranexus-agent.ai/docs/getting-started/quick-start)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-8B5CF6)](https://modelcontextprotocol.io/)

[English](./README.md) | **中文**

</div>

<br/>

大多数 Agent 框架的目标是让 Agent 更*聪明*。NarraNexus 的目标是让 Agent 更*互联*。

封闭的 单个Agent 只是工具。当 Agent 拥有持久记忆、社会身份、人际关系和目标时，它就成为**连接网络（Nexus）**中的参与者——在这个网络中，智能是集体属性，而非模型属性。

NarraNexus 为此提供基础设施：持久记忆、关系感知的上下文、任务调度、模块化能力，以及 Agent 间通信。

## NarraNexus 的独特之处

### 持久上下文

NarraNexus Agent 通过长期记忆、事件记忆和关系感知检索在对话之间保持上下文。Agent 能从过去的交互中延续，而非每次从头开始。

### 可组合运行时

Memory、Awareness、Chat、RAG、Jobs、Skills、Social Network 和 Matrix 等核心能力作为独立模块运行。每个模块管理自己的工具、数据和生命周期，使系统易于扩展和定制。

### 互联 Agent

Agent 通过基于 Matrix 协议的消息系统相互通信，并使用 MCP 工具与其他 Agent、外部工具和后台工作流协调。

## 快速开始

### 在线版本（即将推出）

在浏览器中直接体验 NarraNexus：

> [启动 NarraNexus](https://www.narranexus-agent.ai/)

### 下载桌面应用（仅限 macOS）

从 GitHub Releases 下载最新桌面应用，选择以 `.dmg` 结尾的文件。

> [下载最新版本](https://github.com/protagolabs/NarraNexus/releases)

### 从源码安装

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

脚本会自动检测操作系统（Linux / macOS / Windows WSL2）并处理一切——Python、Docker、Node.js、MySQL、依赖安装和配置。

安装完成后，你会看到下图所示的界面。然后：
1. 在浏览器中打开 `http://localhost:5173`

    a. 选择 **Local** 或 **Cloud（即将推出）** 模式注册账号。

    b. 按照屏幕提示设置 API Key。详见 [LLM 供应商配置](#llm-供应商配置)。

    c. 开始聊天！
2. 打开 `http://localhost:8000/docs` 查看 API 文档。
<br/>

<p align="center">
  <img src="docs/images/install-interface-v2.png" alt="安装界面" />
</p>

<p align="center">
  <em>安装完成——准备打开界面</em>
</p>

更多详情请参阅文档中的[安装说明](https://www.narranexus-agent.ai/docs/getting-started/quick-start)。

## LLM 供应商配置

NarraNexus 使用**三槽位**架构接入 LLM 服务：

| 槽位 | 协议 | 用途 |
|------|------|------|
| **Agent** | Anthropic | 核心推理——驱动 Agent 的思考、工具调用和多轮对话 |
| **Embedding** | OpenAI | 将文本转换为向量，用于 Narrative 匹配和语义搜索 |
| **Helper LLM** | OpenAI | 轻量任务——实体提取、摘要生成、模块决策 |

### 配置方案

| 方案 | 你需要的 | 效果 |
|------|---------|------|
| **[NetMind.AI Power](https://www.netmind.ai/)** | 一个 API Key | 覆盖全部 3 个槽位，最快上手 |
| **Claude Code 登录 + OpenAI** | Claude Code CLI 登录 + OpenAI API Key | Agent 通过 OAuth（有免费额度），其余通过 OpenAI |
| **Anthropic + OpenAI** | Anthropic API Key + OpenAI API Key | 完全控制两个供应商 |
| **自定义端点** | 任何 Anthropic/OpenAI 兼容 URL | 适用于代理、自托管模型或其他供应商 |

> **注意**：目前 Embedding 仅支持 **OpenAI 官方 API** 和 **NetMind.AI Power**。

配置可通过安装向导（桌面应用）或 LLM Providers 面板（Web 界面，点击头部的 CPU 图标）完成。配置存储在 `~/.nexusagent/llm_config.json`。

### 可选 API Key

#### 配置长期记忆（EverMemOS）（即将推出）

EverMemOS 为 Agent 提供长期情景记忆。

| 配置情况 | 效果 |
|---------|------|
| **不配置** | Agent 正常工作，记忆功能不可用 |
| **仅配置 LLM Key** | 记忆提取启用，语义搜索需要额外 Key |
| **全部配置** | 完整的长期记忆——基于云端，无需 GPU **（推荐）** |

你也可以随时手动编辑 `.evermemos/.env`。详见 [EverMemOS 文档](https://github.com/EverMind-AI/EverMemOS)。


## 核心特性

| 特性 | 描述 |
|------|------|
| **叙事记忆** | 对话被路由到语义故事线中，按话题相似度跨会话检索 |
| **热插拔模块** | 独立能力模块（聊天、社交图谱、RAG、任务、技能），各自拥有数据库、工具和钩子 |
| **Agent 间通信** | Agent 通过 Matrix 协议协调——房间、消息、@提及、群聊 |
| **技能市场** | 通过自然语言从 ClawHub 浏览和安装技能 |
| **社交网络** | 实体图谱追踪人物、关系、专业领域和互动历史 |
| **任务调度** | 一次性、定时、周期和持续任务，支持依赖链（DAG） |
| **RAG 知识库** | 基于 Gemini File Search 的文档索引与语义检索 |
| **长期记忆** | 基于 EverMemOS（MongoDB + Elasticsearch + Milvus）的情景记忆 |
| **成本追踪** | 实时计量每次 LLM 调用，按模型分类展示费用明细 |
| **执行透明度** | 每个流水线步骤实时可见——Agent 做了什么决策、为什么、改变了什么 |
| **多 LLM 支持** | Claude、OpenAI 和 Gemini 通过统一适配层接入 |
| **桌面应用** | 桌面端应用，支持自动更新和一键服务编排 |

<br/>

![功能展示](docs/images/FeatureShowcase.gif)
<p align="center"><em>NarraNexus 功能展示</em></p>

<br/>

## Star History

<a href="https://star-history.com/#NetMindAI-Open/NarraNexus&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
 </picture>
</a>

## 致谢

NarraNexus 的长期记忆系统基于 [EverMemOS](https://github.com/EverMind-AI/EverMemOS) 构建，这是一个用于结构化长程推理的自组织记忆操作系统。感谢 EverMemOS 团队的基础性工作。

> Chuanrui Hu, Xingze Gao, Zuyi Zhou, Dannong Xu, Yi Bai, Xintong Li, Hui Zhang, Tong Li, Chong Zhang, Lidong Bing, Yafeng Deng. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[论文]](https://arxiv.org/abs/2601.02163)

## 引用

如果 NarraNexus 对你的工作有帮助，请引用：

```bibtex
@software{narranexus2026,
  title        = {NarraNexus: A Framework for Building Nexuses of Agents},
  author       = {NetMind.AI},
  year         = {2026},
  url          = {https://github.com/NetMindAI-Open/NarraNexus},
  license      = {CC-BY-NC-4.0}
}
```

## 许可证

[CC BY-NC 4.0](./LICENSE)
