---
code_file: src/xyz_agent_context/utils/logging/_ship.py
stub: false
last_verified: 2026-08-10
---

# _ship.py — 网络日志 sink(观测性 push 半边)

## 为什么存在

todo §C 决策 2:部署方 env 决定外发级别
(`NEXUS_DIAG_SHIP=off|meta|full` + URL/TOKEN),`setup_logging` 末位
注册;文件 sink 永远是本源,这里只运副本到收集器
([[../../../../scripts/diag_collector/collector.py|collector]])。

## 设计决策

- **不干扰被观测进程是最高约束**:`enqueue=True` 独享队列线程,业务
  协程零等待;慢收集器只堵自己的 worker,不堵文件 sink(各自队列);
  发送失败**丢批**(文件本源在,缺口走诊断 pull),失败经 `sys.stderr`
  报告——坏掉的 ship 绝不能经 loguru 发声(自递归);
- **级别在 loguru 分发层执行**:meta = 注册时 level=25(AUDIT 及以上,
  含审计镜像行与告警),full = INFO 起;被滤记录零成本;
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
