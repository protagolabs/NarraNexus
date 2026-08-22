---
code_file: src/xyz_agent_context/agent_runtime/run_registry.py
last_verified: 2026-08-21
stub: false
---

# run_registry.py — 按 (agent, surface) 索引活 run 的路由大脑

live-steering 的路由判据:producer 拿到发给某 agent 的新消息,问"这 agent 在**这个 surface** 上有活 run 吗"。
有→steer 进那个 run(下个 step 边界追加进上下文),没有→照旧新起 turn。

**surface-scoped 是命门**:一个 agent 可能同时多个并发 run(多 team 房间 / web 单聊 / job),一条消息只能
进同 surface 的 run——teamA 消息绝不能拼进 teamB turn 的上下文(反串台)。key 是 `(agent_id, surface_key)`。

**为何进程内、内存、不落表**:run 的 steer 句柄(往它 loop 里推的活通道)只存在于**拥有该 run 的进程**;
v1 两个 producer 都与目标 run 同进程——team 消息与 team run 都在 bus-trigger 进程,单聊插话与 web run 都
在 backend 进程。所以"谁产就谁持有 run"。这是 seam(铁律 #20,同 `get_admission_controller`):将来若有
跨进程 producer,接口不变、真相源可迁 Redis/DB view。

## 数据结构与不变量

- 双索引:`_by_run[run_id]→RunHandle`、`_by_surface[(agent,surface)]→run_id`。
- `register`:一个 surface 最多一个活 run;同 (agent,surface) 重复 register **顶替**旧的(旧 run_id 映射被覆盖)。
- `live_run(agent,surface)`:经 surface 索引取回 handle;不同 surface / 不同 agent 天生不匹配(key 就是那对)。
- `release(run_id)`:**只在 surface 仍指向本 run_id 时才清 surface 映射**——被顶替的旧 run 迟到 release 不会
  误踢掉顶替它的新 run。
- `run_id` 不透明(同 steer_inbox):谁 register 谁铸句柄,不必是 late-bind 的 events.event_id;registry 不解释它。
- 同步方法、单事件循环:dict 读改写间无 await 故原子;`get_run_registry()` 进程单例,天然按 owner 分区。

## 放置说明

`run_registry.py` / `steer_channel.py` 平铺进 `agent_runtime/` 而非开 `steering/` 子包:`RunRegistry` 是
通用的"活 run 路由"原语(不止 steering 用),`agent_runtime/` 本就偏平(admission/background_run/client 等
平铺)。若将来 steering 相关文件增多再收成子包。

## 2026-08-21(补)— review 加固:作用域 API + 顶替不泄漏 + 生存性兜底

`registered()` contextmanager(finally release,调用方没法忘);`register` 顶替旧 run 时 pop 旧 `_by_run`
条目(否则泄漏一个 SteerChannel);`RunHandle.is_alive` 可选**同步**探针,`live_run` 命中后校验、死 run 就
清映射并当作无活 run——「忘 release/崩溃」退化成「多起一个新 turn」而非「该 surface 永久失聪」(事故教训
#4/#5)。register/release 各写一行 audit(logger)。is_alive 必须同步(live_run 靠读改写间无 await 的原子性)。
