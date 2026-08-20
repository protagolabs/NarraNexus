---
code_file: src/xyz_agent_context/agent_profile/
last_verified: 2026-08-18
stub: false
---

# agent_profile/ — 改名这件事的唯一写入方

## 为什么是一个领域包，而不是留在 AwarenessModule 里

这套改名事务最早写在 [[_awareness_writes]] 里，因为身份更正笔记住在 Awareness
的 `instance_awareness` 表。但事务本身写的是 `agents` 行、刷的是
`bus_agent_registry`、还接 `is_public` / `created_by`——**没有一样是 Awareness 的
职责**，Awareness 只是它其中一步。

真正的代价在铁律 #3：`PUT /api/auth/agents/{id}` 和 manyfold 的 provisioning
是核心平台路由，它们 import 了一个**可热插拔的 Module**。把 AwarenessModule 从
`MODULE_MAP` 摘掉，不是"少一个功能"，是 route 模块 import 时就 ImportError ——
**后端起不来**。而且当时 mirror 里那句「别再新增写入方，要写就调这个函数」等于
用文档把这个反向依赖固化了。第三轮独立审查指出来的。

现在按仓里既有惯例（`artifact/` `memory/` `message_bus/` `marketplace/`）建成领域包：

```
agent_profile/
├── agent_profile_service.py     公开 seam（协议层）
└── _agent_profile_impl/
    └── profile_write.py         事务实现（私有，不外露）
```

## Awareness 那一步怎么处理

**延迟 + 容错导入**（`profile_write._identity`），不是模块级 import。理由不是
怕循环引用（那只是顺带），而是：如果核心事务在 import 期就硬依赖一个 Module，
"可热插拔"就只是纸面上的。现在 Awareness 不在时，改名的**其中一步**降级并打
warning，其余照常——这才是热插拔该有的含义。

反过来 [[_awareness_writes]] 的 `update_agent_profile_from_args`（MCP 工具的
渲染器）**调用**本包。Module 依赖核心领域包，方向是对的。

**这里有一个双向的延迟导入,说清楚**:本包在改名时按名字 import
`module.awareness_module`(取那两个身份记录写入器),而 [[_awareness_writes]] 的 MCP
渲染器又 import 本包(取事务)。两边都在函数体内,所以 import 期不成环、运行正常;
但**"核心不依赖 Module"这句话不成立**,第十四轮审查指出来的。真实情况是:路由不再在
模块作用域 import Module(那才是"摘掉 AwarenessModule 后端起不来"的成因),而本包在
调用时确实会伸手进 Module 层。

**另外"核心不依赖 Module"要说准**：本包在改名时会按名字 import
`module.awareness_module`，而 Python 必然先导入父包——实测连带拉进 22 个兄弟模块。
延迟导入买到的是**归属**（本包与其上的路由都没有模块作用域的依赖，import 图说明谁
拥有这个事务），**不是 import 期隔离**。[[profile_write]] 的 docstring 写的是准的
版本；这里曾经写得更强，是本次第三次把断言写在验证之前。

## 边界

- 全平台**只有本包**写 `agents.agent_name`。护栏与允许清单在
  `tests/schema/test_only_one_writer_of_agent_name.py` —— **可执行的那一份**。
  mirror 只指过去,不再自带一份会漂的拷贝(那份测试的 docstring 自己也这么主张)。
- 建 agent **不走**这里：本函数的前置是**行已存在**（读不到即 `not_found`）。
  新建 agent 也没有"旧名"可更正。
