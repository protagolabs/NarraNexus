# 自动化 PR Review

本仓库的 PR 会经过一个基于 Claude 的自动代码 review。它不替代人工 review,而是
在人看之前先过一遍,把明显的问题、铁律违规、方言坑等抓出来,降低来回成本。

## 怎么触发

给 PR 打上 **`ai-review`** 标签即可。下一轮轮询(目前约每分钟一次)就会:

1. 读取该 PR 的 diff;
2. 按本仓库的 review 标准(见下)逐项检查;
3. 在 PR 上回一条**结论先行**的中文 review 评论。

只有「新 PR」或「有新 commit」的才会被审——审过且无新提交的会自动跳过,不会重复刷屏。

## review 标准

标准固化在一个版本化的 review skill 里(与被审代码同源演进),核心检查项:

- **CLAUDE.md 铁律**:模块独立可热插拔、不做危险 DB 变更、不给 `agent_loop` 加硬性
  时间/次数上限、不干预用户的 LLM 选择、资源压力用户无感处理等;
- **架构分层**:api → runtime → service → impl → repository 单向依赖,路由不堆业务逻辑;
- **SQLite/MySQL 双方言契约(阻塞级)**:schema 走 `schema_registry`、两套类型都填;
  原生 SQL 必须方言可移植 + 参数化;新增含原生 SQL 的文件需要**真 MySQL 集成测试**;
- **长运行服务安全**:fire-and-forget 协程要挂回调、异常别为了清日志而吞、健康检查到
  L2、生命周期事件落审计表;
- **安全 / 测试 / Tier-2 mirror 文档同步**。

## 严重度与结论

每条 review 以一行**结论**开头:

| 结论 | 含义 |
|---|---|
| ✅ 可以合并 | 无 Critical、无 Important |
| 🔧 修完可合并 | 有 Important,但无 Critical 阻塞 |
| ⛔ 阻塞 | 至少一个 Critical(含铁律违规 / 新原生 SQL 文件缺集成测试) |

问题按 🔴 Critical / 🟡 Important / 🟢 Minor 三级标注,各自给 `文件:行`、原因、修法。

## 增量与对话

- review 会在评论末尾留一个 `reviewed-commit` 标记,下次只看新增的 commit;
- 如果你对某条意见有合理解释(例如「这是有意为之,因为 X」),在评论里回复即可;
  下一轮会把它标记为「已确认(开发者说明)」,不再重复提。

## 说明

- review 输出为中文;严重度严格分级,不注水、也不为了和气而降级。
- 它只发 PR 评论,不改任何代码。
