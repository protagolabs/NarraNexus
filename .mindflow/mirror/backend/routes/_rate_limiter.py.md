---
code_file: backend/routes/_rate_limiter.py
last_verified: 2026-10-28
stub: false
---

# _rate_limiter.py — 进程内滑动窗口限流器（全仓唯一一份）

## 为什么存在

所有 per-key 限流桶（dashboard agents-status、analytics、渠道匿名 webhook、
auth 的 signup / send-code / funnel-report）共用这一个实现。不要另起第二个限流器。

## 设计决策

- deque 滑动窗口，周期性 cleanup 防止一次性 key 无限增长。
- **拒绝路径不分配 key 是防御性保证，不是封顶**：limit>0 时新 key 总被放行并分配；
  caller-chosen key（email）的 key 空间只能靠调用点把可信的 per-IP 桶放在前面来封顶。
- `retry_after(key)`：被拒后给 429 `Retry-After` 用。被拒 key 的 deque 恰有 limit
  条窗口内记录，最老一条出窗即空出名额；向上取整、最小 1；无状态 key（limit<=0
  退化配置）返回整个窗口。
- `env_limit(name, default)`：运维可调限额的共享解析器。未设/空/非整数/<1 一律回默认，
  不在 import 期炸 backend；0 不被接受，因为 `allow` 把 limit<=0 当「全拒」。

## Gotcha

- **触发**：多 worker 部署 → **症状**：实际限额 = 单进程限额 × worker 数 →
  **根因**：状态在进程内存里。需要全局一致限额时得换 Redis 版。
- **触发**：测试里不重置模块级 limiter → **症状**：跨用例 429 → **根因**：限流器是
  模块单例；测试 fixture 用 monkeypatch 换新实例。

测试：`tests/backend/test_auth_funnel_observability.py`、
`tests/backend/test_signup_abuse_limits.py`。
