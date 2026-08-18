---
code_file: src/xyz_agent_context/module/awareness_module/_awareness_writes.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 比较上提共享,判据也从 rowcount 换成回读

两处改动,都是和 [[auth.py]] 那半边对齐:

1. 值相等短路改调 [[entity_schema]] 的 `agent_field_matches` /
   `normalize_agent_text`(行为不变 —— 本文件本来就是 strip 后比较,
   只是不再自带一份)。原因见那份 md:同一个输入,本文件判「没变化」而
   HTTP route 判「要写」,两个写入方对 `agents` 行的等价规则不一致。
2. `affected = await repo.update_agent(...)` / `if affected <= 0` 这对**去掉了**,
   改成回读 + 同一个谓词核对。短路已经挡掉 no-op 那一支,但「**真有改动而
   驱动报 0**」这一支原来仍然会答 "Error: the update did not apply" ——
   `cursor.rowcount` 在 MySQL 上数 CHANGED 行,而 agent 读到这句会去重发一次
   其实已经成功的改名。这正是 HTTP 侧 2026-08-17 那次修掉的推断。

**返回串一个字没动**:DirectStore 与 HTTP twin 靠这些串保持 byte-identical
(`backend/routes/agents/profile.py` 直接把它当响应体)。改的是**什么条件下**
返回那句,不是那句本身。

> 措辞更正:本文件 2026-08-05 那次只拆了 **no-op 短路**这一支,module docstring
> 里「陷阱已 defused」的说法当时仅对 agent 侧成立,且 HTTP twin 直到 2026-08-17
> 都还带着原样的 rowcount 判据。现在两条路径在这个 trap 上才真的同语义。

补记(同日 review 第二轮):回读判失败那条分支现在**记一行 WARNING**(字段名 +
「并发覆盖或没落库」),与 [[auth.py]] 同分支对齐。原来一个日志都没有 ——
真发生时唯一的记录是递给模型的那句话,若是并发写造成的,日志与 DB 里没有任何
东西能和它对上(踩着 CLAUDE.md 事故教训 #5 的反面)。**返回串一个字没动**。

再补(第三轮):import 从 `xyz_agent_context.schema.entity_schema` 深引改为
`xyz_agent_context.schema` 门面。本文件在 `schema/` 之外,用门面没有环;
而 [[api_schema]] 与 manyfold 路由**必须**保留深引(门面反过来再导出
api_schema 的模型,引门面成环),那两处已就地注明原因。

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
