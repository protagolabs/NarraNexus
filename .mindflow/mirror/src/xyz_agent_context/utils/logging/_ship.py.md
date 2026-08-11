---
code_file: src/xyz_agent_context/utils/logging/_ship.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11(三)— 第 4 轮收口

Tier-1 docstring 的 URL 解析段同步删除 manyfold 嗅探描述(镜像与
docstring 曾互相矛盾);`_setup` 的 `from ._ship import` 移入 try
("shipping is optional, logging is not" 必须覆盖 import 期失败);
发现文档防御性解析(非 `{"ingest": {...}}` 形状按无效处理,收集端
`/v1/config` 同步做形状校验以免发送端静默不解析);atexit 扫尾补
`client.close()`。

## 2026-08-11(二)— 安全轮:默认 off 合入 + 发现 URL 白名单 + 并发/字节修正

- **默认改回 off**(`_DEFAULT_MODE`):review Critical——"默认开"所
  依赖的同意基础(首次告知 UI + optout 写入)不在本 PR;默认值与其
  同意基础必须**同批到达**,UI PR 落地时翻 full。顺带消解"合并即
  dev 环境未经 ops 同意定时打 prod 域名";
- **发现 URL 白名单**:`https` + `*.narra.nexus` 才接受——发现文档
  来自公开端点且决定用户日志去向,被劫持/配错时拒绝切换、保留旧值;
- **长生命周期 httpx.Client**(发现与发送共用):每请求新建客户端 =
  一次投递吃两回 TCP+TLS 冷握手、各自只有 2s——健康但握手慢的
  收集器会被误判成 5 连超时而熔断;
- manyfold env 嗅探移出(通用工具不读别家集成的 env)——staging
  判定移到 run.sh 容器模式,派生注入 `NEXUS_DIAG_ENV`;
- 缓冲改存 **encoded bytes**(len(str) 数字符,中文正文约 12MB 才触
  "4MB"阈值);熔断状态加 `_state_lock`(RLock);4xx 应答同时清
  半开(收集器活着即视为探测成功,否则半开态粘住)。

## 2026-08-11 — 遥测化重构 v2(与 manyfold 负责人对齐后)

从"运维特性(平台注入 env)"改为**产品遥测(用户同意)**,对
manyfold 的观测性依赖清零,且顺带覆盖本地 DMG 用户:

- **同意模型**:env 显式覆盖(off/meta/full,也是测试套件灭火开关,
  见 tests/conftest.py)> `~/.narranexus/telemetry_optout` 标记文件
  (设置 UI 写)> **缺省 full 开**(首次告知在 UI);
- **URL 发现**:代码只写死一个入口
  `https://agent.narra.nexus/telemetry/v1/config`(prod collector 的
  `/v1/config` 伺服,env 可覆盖),响应是 env→ingest URL 的 map,
  发送端按自身标签选路(显式 `NEXUS_DIAG_ENV` > manyfold webhook
  含 api-staging 判 staging > NARRA_SURFACE)——staging 噪声进
  dev-agent.narra.nexus,URL 轮换零发版。**惰性解析**:只在 worker
  线程首发时拉取,TTL 1h、失败退避 60s、stale-if-error——进程启动
  与测试永不碰网;解析不出 = 静默丢批(stderr 一次),不入熔断账;
- **去 token**:仓库开源,任何内置/共享 secret 都等于公开——收集端
  转公开形态,防线是滥用控制;复用 manyfold token 被否(受众错位、
  我方无法校验、本地面根本没有)。`NEXUS_DIAG_SHIP_TOKEN` 留作私有
  收集端旋钮。

# _ship.py — 网络日志 sink(观测性 push 半边)

## 为什么存在

部署方 env 决定外发级别(`NEXUS_DIAG_SHIP=off|meta|full` +
URL/TOKEN;env 旋钮清单见 `.env.example`),`setup_logging` 末位注册;
文件 sink 永远是本源,这里只运副本到收集器
([[../../../../scripts/diag_collector/collector.py|collector]])。
pull 半边 = manyfold 诊断端点,两者刻意零共享机制。

## 设计决策

- **不干扰被观测进程是最高约束**:`enqueue=True` 独享队列线程,业务
  协程零等待;慢收集器只堵自己的 worker,不堵文件 sink(各自队列);
  发送失败**丢批**(文件本源在,缺口走诊断 pull),失败经 `sys.stderr`
  报告——坏掉的 ship 绝不能经 loguru 发声(自递归);
- **级别在 loguru 分发层执行**:meta = 注册时 level=25(AUDIT 及以上,
  含审计镜像行与告警);full = **跟随文件 sink 的 resolved_level**——
  排障开 DEBUG 时远端必须同步可见,硬编码 INFO 恰好在最需要的场景
  失效(review 修);被滤记录零成本;
- **熔断**(review 两轮):连续 5 次**瞬态**失败 OPEN 60s——期间
  记录在门口直接丢、不入缓冲(否则死收集器 = 无界 enqueue 队列反噬
  被观测进程内存)。冷却后 **HALF-OPEN 真实现**(第二轮修:此前只
  存在于注释——探测批失败**立即**重开,不再重攒 5 次,消灭每 70s
  白烧 10s 的稳态;成功则全闭)。**4xx 永久拒绝不入熔断账**:413 是
  "这批不合格"不是"收集器死了",丢批+单独告警;
- **按字节分批**(第二轮修):序列化长度 ≥4MB 提前 flush(收集端
  线上 8MB 封顶的一半,留 gzip 余量)——纯条数分批可能撞上按字节
  封顶的接收端,批批 413;缓冲改存序列化行,字节计数零额外开销;
- **atexit flush**(review 两轮):**单个模块级 handler + WeakSet**
  ——per-instance 注册会把每个构造过的 sink 钉在 atexit 表里(多
  service 进程与测试线性累积,退出时串行叠 2s 超时);熔断开着时
  flush 直接丢弃不发,不对已知死收集器烧超时;
- **身份两层**:每条记录平铺信封字段(env/runtime_id/host/service +
  run_id/event_id extras);sprite 单 runtime,信封即用户归属;
- **崩溃尾巴补发**:注册时把当日既有文件末 200 行按 `backfill: true`
  发一次——重复行收集端可容(读时去重),丢崩溃现场不可容;
- 批:200 条或 3s;gzip;2s 超时;失败第 1 次与每 50 次提示一次。

## Gotcha

- 云端 executor 进程不走 `setup_logging`(裸 logger.add)→ 暂不入
  sink,决策 1 布局统一时一并收(todo §C 已记);
- 定时 flush 是 daemon 线程,不阻塞解释器退出——代价是进程秒退时
  最后 <3s 的批靠下次启动的 backfill 找回。
