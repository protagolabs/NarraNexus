---
code_file: backend/middleware/login_ips.py
last_verified: 2026-10-29
stub: false
---
# login_ips.py — 记录每个用户正在用哪些公网 IP 鉴权（滥用 IP↔账号联动的数据源）

## 为什么存在

2026-10-28 事件后要求"封禁时 IP 和账号一起封"。IP 半边已由 deploy 仓哨兵完成（扫描 IP
→ spool → 宿主防火墙 DOCKER-USER 丢包），但它按设计不碰账号。要连带封账号，哨兵需要知道
"最近从这个 IP 活跃的是哪些账号"——这张 `instance_login_ips` 表就是答案，本模块是它唯一
的写入方。

## 两条写入路径，缺一不可

- **登录**（`backend/routes/auth.py` netmind-login）：`record_login_ip` 无条件写。
- **每个鉴权请求**（`backend/auth.py` JWT 分支、账号状态门之后）：
  `note_authenticated_request` 按 (user, ip) 每 `RECORD_THROTTLE_SEC`（10 分钟）最多写一次，
  后台执行。

第二条才是让联动真正生效的那条（auto review I-1 指出）：JWT 活 7 天、可换地址用。只在
登录时记，`last_seen` 停在签发时刻，哨兵 24h 窗口几乎查不到人；更关键的是看不到 token 被
**重放**的 IP——"A 地址登录、拿 token 去 VPS 扫描"的攻击者在扫描 IP 上没有任何记录。按请求
节流记录后，`last_seen` = 该 IP 上的真实最近活动，24h 窗口既能命中，又比"7 天窗口"更少误伤
共享出口。

## 契约与坑

- **只记全局可路由 IP**（`is_global`）。拿到非公网"客户端 IP"意味着
  `FUNNEL_TRUSTED_PROXY_HOPS` 和边缘拓扑对不上了；记下来会把表灌满 nginx 内网地址、让联动
  静默失明。登录路径在云上打 warning（本地是回环，按设计跳过不刷屏），请求路径静默跳过。
- **保留期** `RETENTION_SEC`（30 天）：机会式清理，每进程每小时最多一次。必须远大于哨兵
  联动窗口（24h），否则读方查的那几天被清掉——同一种静默失效。按 `last_seen` 删，不按
  `created_at`（长期复用、最近才活跃的 (user, ip) 不能误删）。**不随账号删除级联**：被封后
  注销的攻击者 IP 在保留期内留着是有意的（取证），保留期本身是上界。
- **全程 best-effort**：记录失败绝不让登录/请求失败；后台任务挂 done-callback 打日志
  （CLAUDE.md 事故教训 #2：fire-and-forget 必须配回调），并持强引用防止被 GC。
- **节流表是进程内优化**，丢了只是多一次 upsert，不是需要跨进程 seam 的共享状态；有
  `_THROTTLE_MAX_ENTRIES` 上界防泄漏。pair 在调度**前**占位，并发请求只排一次写。
- **清理在后台跑**：`_maybe_prune` 只调度不等待——登录路径 await `record_login_ip`，
  不能被一个区间 DELETE 拖慢。时钟在尝试**之前**打戳，持续失败的 DELETE 每小时只试一次。
- **后台写失败要释放占位**：`note_*` 先占位再调度（并发突发只排一次写），但写没落地时
  `_record_in_background` 把占位 pop 掉，否则一次 DB 抖动会让该 pair 盲 10 分钟。
- **长连接 presence**：`start_connection_presence` 给 WebSocket 用——一条 WS 承载一个 run，
  run 可跑几十小时（铁律 #14），只在建连时记会让 `last_seen` 在 run 中途滑出读方窗口。任务
  每 `RECORD_THROTTLE_SEC` 重记一次，连接断开后一个周期内自行退出，调用方无需收尾；一个
  调用点覆盖新 run / 重连 / 跨进程跟随三条分支。
- **跨仓契约**：`RECORD_THROTTLE_SEC` / `RETENTION_SEC` 被 deploy 仓哨兵
  `account_linker.WRITER_REFRESH_SEC` / `WRITER_RETENTION_SEC` 镜像，用来在启动时拒绝落在
  (刷新, 保留) 区间外的 `ACCESS_LINK_WINDOW_SEC`。改一边必须改另一边。
- **日志带异常用 `logger.opt(exception=True)`**：loguru 不认 stdlib 的 `exc_info=True`
  （会被当成格式化参数丢掉），写成那样告警里就没有原因了。测试钉住。
- **`get_db_client` 在调用时解析**（不在模块加载时绑定），与 auth 中间件用同一个工厂，
  测试桩一致生效。
- 写入字段：`pair_key=f"{user_id}|{ip}"` 对上表的单列唯一冲突目标；不写 `created_at`，
  冲突时保留首见时间；`last_seen` 是 epoch 秒。表设计的理由见 `schema_registry.py` 的 mirror。
