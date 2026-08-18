---
code_file: src/xyz_agent_context/module/awareness_module/_awareness_writes.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 (二改) — 第四个写入方，以及两处审查修正

同日第一版声称「三个写入方收敛成一个」，独立审查发现是**四个**：
`POST /manyfold/agents` 的 upsert 分支同样覆盖 `agent_name`（见 [[agents]]
二改条目）。断言写在验证之前，正是本仓栽过的同一类毛病。现在可以一条命令重推：

```bash
git grep -nE '(insert|update)\(\s*"agents"' -- backend src
# → 只剩两处，都是 INSERT（建 agent），没有 update：
#   backend/routes/manyfold/agents.py（新建分支）
#   src/xyz_agent_context/bundle/importer.py（bundle 导入）
```

改名**没有**不经过 `apply_agent_profile_change` 的路径了。

两处审查修正：

1. **`updated_fields` 不再身兼二职**。原来错误分支往它塞「没落地的字段」，与它
   自己的注释（"were written"）相反：同一个名字在 `ok` 两侧含义颠倒。今天两个
   调用方都先判 `ok` 所以不出错——但下一个做写入埋点的人会直接读它、把失败计成
   写入，而这种错**永不抛异常**，只让指标悄悄偏高。拆出
   `unapplied_fields`。`auth.py` 的错误文案跟着改到新字段（那条分支当时零覆盖，
   改漏了会变成空串 `"The update did not persist: "`，已补测钉住）。
2. **`created_by` 进了 [[entity_schema]] 的谓词**，因为它现在会作为
   `extra_updates` 流经等值短路，而该谓词对未登记字段**故意抛 ValueError**。
   比较方式选的是**逐字节相等，不做 `normalize_agent_text`**：那个助手是给人读的
   展示文本 strip + 截长的，把一个当查找键用的标识符悄悄改形，正是「行被改成一个
   谁也解析不出来的 owner」的成因。

## 2026-08-18 — 改名事务下沉成 `apply_agent_profile_change`，三个写入方共用

深圳线下第二轮 P1（prod `agent_4a0ae5f40af2`，8/14 复测）。取证：`agents.agent_name`
= 「小绿」、`bus_agent_registry.description` = `'小绿: 精通各地美食推荐'`（都对），
而 Awareness profile 里躺着 8/14 15:48 那条身份更正：

> `- 2026-08-14: renamed ... to 「美食家」. You are 「美食家」. 「小绿」 is no longer your name`

方向是反的。agent 先被自己的工具从 小绿 改成 美食家（写下这条笔记），随后被
**界面**改回 小绿——而 `PUT /agents/{id}` 只写列 + 刷名录，**不追加笔记**。于是
profile 每轮原文注入系统提示，用平台的口吻告诉 agent「小绿 不是你的名字」。问
「你是谁」答「美食家」，两次，且它有明确依据。

**这比缺笔记更糟**：笔记是平台声明，agent 会信。所以本次不是"再给 PUT 补一处调用"
——补调用的做法正是 2026-08-04 已经用过的，它撑了 10 天就被第二个写入方绕过。
改成：**改名事务不再由调用方拼装**。

- 新增 `apply_agent_profile_change(db, agent_id, *, new_name, new_description,
  extra_updates)` → `AgentProfileWrite`（frozen dataclass）。名/描述/额外字段的
  归一 + 等值短路 + 单次行写 + 回读核对 + **身份更正** + **刷名录**，全在里面，
  调用方无法只做其中一步。
- `update_agent_profile_from_args` 降级成**渲染器**：把结构化结果格式化成工具历来
  那串字节。DirectStore / [[profile]] 孪生路由的 byte-parity 不受影响（
  `test_agent_profile_tool.py` + `test_profile_seam_route.py` 23 例原样绿）。
- 返回**结构化**而非字符串，是因为另两个调用方是 HTTP 路由，欠客户端一个状态码。
  靠匹配英文散文推状态码，第一次改措辞就断。`error_kind`
  （`nothing_to_update` / `not_found` / `empty_name` / `too_long` / `not_applied`）
  是给机器的，`error` 是给模型读的那一句，两者不混用。
- `extra_updates` 收 `is_public`：它没有身份语义，但放进同一次调用才能保持**单次
  行写**，否则会出现行被改了一半的窗口。它**同样走等值短路**——第一版漏了，
  `test_re_toggling_to_the_current_visibility_writes_nothing_and_succeeds` 当场
  抓到。这条短路不是优化，是本函数方言无关性的地基（no-op 写在 MySQL 返 0、
  SQLite 返 1，下游全部建立在"永远不必解释这个数字"之上）。
- **「无变化」那一支现在也刷名录**（原来提前 return）。理由沿用 [[auth]] #320：
  sync 自己吞失败只返 False，原值重存是用户最自然的重试方式，它不该恰好是唯一
  跳过修复的路。现在三条路径在这点上同语义。

⚠ 给后来者：**别再新增 `agents.agent_name` 的写入方**。要写，调这个函数。理由不是
洁癖——"三个写入方各自记得三件事"就是本条目和 2026-08-04 那条的共同成因。

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
`xyz_agent_context.schema` 门面。本文件在 `schema/` 之外,用门面没有环。

> 更正(第四轮):上一句原来还写着「manyfold 路由**必须**保留深引,因为成环」——
> **假的**。成环只对 [[api_schema]] 成立(它在 `schema/` 包内,门面反过来导出它的
> 模型);manyfold 在 `backend/` 下,引门面不可能成环(门面不 import backend,
> 这正是本仓的依赖方向)。它当时深引的真实原因只是 `StrippedText` 还没进门面。
> 现在 `StrippedText` 已导出,manyfold 四个符号全走门面。这是同一类毛病的第三次:
> **给一个观察到的现状编一个比事实更强的理由**。

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
