---
code_file: frontend/src/components/dashboard/ConcurrencyBadge.tsx
last_verified: 2026-04-13
stub: false
---

# ConcurrencyBadge.tsx — Intent

## 为什么存在
给 **public 非自有** agent 的 header 提供"大概多忙"视觉提示——比如 `×3-5`。Owner 自己的卡片**不用**这个组件（verb_line 已给出确切数字和类型）。

## 当前行为（v2.1.1+）
```
agent.owned_by_viewer === true    →  return null
agent.running_count_bucket === '0' → return null
otherwise                          → <span>×{bucket}</span>
```

## 历史
- **v2**：对 owned 显示 `×{running_count}` 精确整数，对 public 显示 `×{bucket}`
- **v2.1.1 移除 owned 分支**。原因：用户反馈 `"Job ×4 / Callback ×2"` 语义混乱——`running_count` 是 sessions + running_jobs + in_progress_instances 的**求和**，和 kind 标签指向的不是同一批对象。verb_line 以人话讲清楚类型和数量，远比孤立的 `×N` 好

## 为什么 public 保留
Public 非自有用户看不到 verb_line（server 不返回——防信息泄漏）。只能看到 name + kind + bucket。没这个 badge，用户不知道 "1 个人用" 还是 "50 个人用"。
并发桶本身也是**隐私措施**——暴露精确 running_count 可被用作流量推断攻击（security rev-1 M-1），public 只给 bucket 字符串。

## 数据契约
- 消费 `AgentStatus`（discriminated union）
- Public 分支读 `running_count_bucket: '0' | '1-2' | '3-5' | '6-10' | '10+'`
- 永远不应 render 精确数字——即便偶然拿到，也别暴露

## Gotcha
- **不要给 owned 加回 `×N`**。写死在 v2.1.1 commit message。如果未来 UX 要"卡片首屏显示数字"，优先改 `humanize_verb` 产出，而不是恢复这个 badge
- 组件极简（20 行），但它是**权限模型在 UI 层的执行点**——不要加逻辑让 owned 走额外分支
