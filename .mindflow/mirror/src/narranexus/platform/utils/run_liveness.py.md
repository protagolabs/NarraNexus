---
code_file: src/narranexus/platform/utils/run_liveness.py
last_verified: 2026-09-10
stub: false
---

# utils/run_liveness.py — 「这个 run 还活着吗」的唯一跨进程规则

## 为什么存在

`run_is_live`（心跳新鲜度判定）与其常量原本住在 [[run_recorder]]。熔断器
（[[circuit_breaker]]）需要同一条规则来判断半开探测的认领者是否可能还在跑，于是 lazy
import `run_recorder`；而 `run_recorder.sweep_stale_runs` 又 lazy import 熔断器——两个
函数内导入互相掩护一个 loop↔runtime 环，任何人把其中之一提到模块级，进程启动即
ImportError（PR #394 review I4）。把纯数据 + 算术搬到 `utils` 叶子后，两边都能模块级导入，
环消失。

## 内容

`HEARTBEAT_INTERVAL_S`(30s)、`RUN_STALE_AFTER_S`(3 次心跳)、`STATE_RUNNING`、
`parse_db_utc`（SQLite ISO 串 / MySQL datetime → aware UTC）、`run_is_live(events_row)`
（`last_event_at` 优先、回落 `started_at`，无可解析时间戳时 fail-open 视为活）。

## 上下游

[[run_recorder]] 原样 re-export（同一对象、`__all__` 不变），既有调用方不动；
[[circuit_breaker]] 模块级导入。两处活性判定必须是**同一个对象**——
`test_run_recorder.py::test_breaker_and_sweep_share_one_liveness_rule_without_a_cycle` 钉住。

## 约束

只读判定：绝不能据此停止或改动一个活的 run；长 run 持续心跳就一直算活（铁律 #14）。
