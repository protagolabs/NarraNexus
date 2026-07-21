---
code_file: scripts/manyfold_trigger_experiment/seed_experiment.py
last_verified: 2026-07-20
stub: false
---

# seed_experiment.py — 触发面外移验证的最小数据 seed

## 为什么存在

`fake_manyfold.py` 要驱动一个真能跑起来的 agent,需要三样东西到位。这个脚本
把它们塞进本地库(与运行的 Nexus 同一个 `DATABASE_URL`,默认 sqlite
`~/.narranexus/nexus.db`):

1. **复用现成 agent**(而非新建):现成 agent 已有 provider slot、能真跑 LLM;
   新建 agent 可能缺 awareness/basic 等实例而跑不起来。`--list-agents` 列出
   带 slot 的候选。
2. **stub lark 凭证**:让 `LarkModule` 渲染出 "Mode: LARK CHANNEL — reply via
   lark_cli"。关键字段——`app_secret_encrypted`(base64 非空 →
   `receive_enabled()`)、`permission_state.user_oauth_completed_at`(→
   `current_click_stage()=='completed'`,coach 说"回复"而非"去配置")、
   `auth_status='user_logged_in'`、`owner_open_id='ou_alice'`(让
   `--sender ou_alice` 被判为 owner → 全信任)。secret 是**假的**:抽象验证
   只断言 agent 发出 lark_cli 调用,真发送要真 bot。
3. **到期的 scheduled job**:给"钟"一个可镜像、可开火的目标
   (`interval_seconds=3600`,`next_run_time=now`)。

## 上下游

- 用 `JobRepository.create_job` + `TriggerConfig` 造合法 job(避免手写 SQL 漏
  字段);lark 凭证走 `db.insert("lark_credentials", …)`。
- 幂等:重跑先删同 agent 的 lark 凭证 + `[mf-exp]` 前缀的 job。`--clean` 清掉
  seed 痕迹,把库还原到实验前。

## Gotcha

- 必须与运行的 Nexus 用**同一个 DATABASE_URL**;container/gated 模式用 sqlite,
  别让 seed 落到默认 MySQL。
- seed 在起 stack **之前**跑(直连文件);stack 起来后 sqlite 经 proxy 串行化,
  再直连文件写会有锁风险。
