---
code_file: src/xyz_agent_context/utils/logging/_ship.py
stub: false
last_verified: 2026-08-10
---

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
