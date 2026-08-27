---
code_file: tests/channel/test_ingress_breaker_persistence.py
stub: false
last_verified: 2026-08-24
---
# test_ingress_breaker_persistence.py — 冷却必须活过进程

钉 [[ingress_guard.py]] 的读写分层：滑窗纯内存、tier/冷却写穿。

- 重启后仍在冷却中（不发新预算）
- 重启后冷却已过 → 放探测，但 **tier 保留**（否则每次部署都把惯犯打回最便宜
  那档）
- 重启后再犯 → 升到**下一档**，不是第一档
- **62 条入站消息只允许 1 次持久写**——热路径一旦开始写库，一场入站风暴就
  变成一场 DB 风暴，等于用一个故障换另一个故障
- 保留期清扫只删闭合的行，带升级记忆的行不许被「安静」清掉
- 未达阈值的流量**一行都不落**

与凭据熔断器的纯内存结论相反，不矛盾：那个描述的是**活着的** subscriber
状态，寿命不同。
