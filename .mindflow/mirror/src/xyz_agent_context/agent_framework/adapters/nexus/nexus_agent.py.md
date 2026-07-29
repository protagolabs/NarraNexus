---
code_file: src/xyz_agent_context/agent_framework/adapters/nexus/nexus_agent.py
last_verified: 2026-07-29
stub: false
---
# adapters/nexus/nexus_agent — NexusAgent driver

三件事:遗留签名→TurnRequest(模型配置读同一个 claude_config contextvar——平台 provider 皆 anthropic 协议;bearer_token 补 Authorization 头经 llm_extra 透传)、默认子进程跑 runner(NEXUS_POWER_INPROCESS=1 走进程内,executor/测试用;读行 32MB limit 手动缓冲)、response.done 每路必发恰一次。oauth 凭据显式拒绝(nexus 直驱 API,留在 claude_code)。取消=轮询+SIGTERM 进程组。capabilities={event_log}(声明与实现同批)。

## 2026-07-29 — 温进程池(TTFB 3.4s→1.1s)

_WarmRunnerPool:预 spawn 已完成全部导入(含 litellm 1.8s/215MB)的 runner
闲置待命;acquire 即用即耗(单回合单进程,隔离不变),取用后后台补位;
NEXUS_POWER_POOL_SIZE 定池(默认 1,0 关;每闲置进程 ~350MB RSS,速度换
内存的显式取舍)。driver 构造即预热(首回合与导入重叠)。atexit 收割闲置。
