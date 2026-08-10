---
code_file: src/xyz_agent_context/module/awareness_module/_awareness_writes.py
last_verified: 2026-08-10
stub: false
---

# _awareness_writes.py — update_agent_profile 的共享实现（AgentDataStore seam 单点）

## 为什么存在（PR-9）

`update_agent_profile_from_args` 是 update_agent_profile 工具那套**改名事务**的唯一实现：
写 name/description、往 Awareness profile 追加一条身份更正、同 owner 重名如实告知、写完立刻刷
同伴名录——四件缺一件就退回 prod evt_1f9c6680 的 bug（详见 [[awareness_module]] 2026-08-04）。
从 awareness_module.py hoist 到这里，让 seam 的 DirectStore（本地）与 backend 孪生路由
[[profile]]（云）都调**同一个函数** → 两条路径 byte-identical，改名事务逻辑不在 in-process/Http
之间分叉。身份笔记字符串助手（build/merge + 常量）与两个 DB 助手（_same_owner_name_holder /
_record_identity_change）也一并搬来——它们只被这个 writer 用；放一起还打破 import 环
（awareness_module.py 把工具委托给 seam，故不能反向 import 本文件）。

## 契约

- 返回 **str**（工具历来的状态串，动态：改了哪些字段 + 重名 note），从不因已处理分支抛异常。
- 方言安全（铁律 #6）：AgentRepository / InstanceRepository / InstanceAwarenessRepository +
  db.get，无裸 SQL。**MATCHED-vs-CHANGED rowcount 陷阱**（update_agent 返回 cursor.rowcount=
  SQLite matched / MySQL changed）在写之前被 name/description 两处**等值短路**化解：重复存同值
  返回 "No changes needed"，两方言一致，而不是云上假报 "did not apply"。**别动这两个短路**。
- **长度上限**：name/description 都绑 `AGENT_TEXT_MAX_LENGTH`(255，读模型 `Agent` Field + MySQL VARCHAR(255) 同源)。>255 写会让 agent 行读不出来(get_agent→pydantic ValidationError，NetMindAI-Open#71)且方言分叉(sqlite TEXT 收/MySQL 1406)。检查放**共享 fn**里 → Direct/Http 逐字节同拒(store parity invariant)；路由 body 故意不加 `Field(max_length)`(见 [[profile]]：会 422 抢在 fn 前、破 byte-parity)。
- 未迁：update_awareness 另在 seam（早前 PR）；本文件只管 profile。
