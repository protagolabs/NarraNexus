# Git Squash Merge — 干净 PR 的工作流

## 核心概念

**Squash Merge**（压缩合并）：将一个分支上的多个 commit 压缩成一个单独的 commit，合并到目标分支。

解决的问题：开发时频繁小 commit（调试、WIP、fix typo...），但 PR 到 public repo 时希望历史干净、每个 PR 只有一个有意义的 commit。

---

## 方法一：GitHub 界面 Squash Merge（最简单）

合并 PR 时点击 "Squash and merge" 按钮（而非默认的 "Merge commit"）。

- GitHub 自动把分支上所有 commit 压成一个
- 开发分支历史不受影响
- 零操作成本

**适用场景**：PR 已经推到 remote，直接在 GitHub 上操作。

---

## 方法二：本地 `git merge --squash`（手动控制）

当你想在本地控制 commit message，或者需要从开发分支创建一个干净的 PR 分支时使用。

### 完整流程

```bash
# 1. 确保 main 是最新的
git fetch origin main
git checkout -b pr/feature-name origin/main    # 基于 main 创建干净的 PR 分支

# 2. 把开发分支的所有改动压缩合入（不会自动 commit）
git merge --squash feat/my-dev-branch

# 3. 写一个干净的 commit message
git commit -m "feat: 完整的功能描述"

# 4. 推送 PR 分支
git push -u origin pr/feature-name

# 5. 在 GitHub 上基于 pr/feature-name 创建 PR
```

### 关键点

- `git merge --squash` 只合并改动，**不自动 commit**，给你机会写一个好的 message
- 开发分支 `feat/my-dev-branch` 完全不受影响，可以继续开发
- PR 分支是一次性的，合并后可以删掉
- **永远不需要 force push**

---

## 为什么不用 `git rebase -i` 或 `git reset --soft`？

| 方法 | 问题 |
|------|------|
| `git rebase -i` | 改写历史 → 和 remote 分叉 → 必须 force push → 协作时容易覆盖别人的代码 |
| `git reset --soft` | 同上，改写历史 |
| `git merge --squash` | 不改写任何历史，创建新分支新 commit，零风险 |

rebase 和 reset 在**纯个人、未推送**的分支上没问题，但一旦分支已经 push 到 remote（或有人基于它开发），改写历史就会带来麻烦。

---

## 实战示例（本项目）

```bash
# 开发分支有 26 个碎 commit
git log --oneline main..feat/20260226_electron_desktop_app | wc -l
# → 26

# 创建干净 PR 分支
git fetch origin main
git checkout -b feat/20260304_frontend_improve origin/main

# Squash merge
git merge --squash feat/20260226_electron_desktop_app
# → Automatic merge went well; stopped before committing as requested

# 查看：26 个 commit 的改动全在 staging area
git diff --cached --stat
# → 58 files changed, 18421 insertions(+), 66 deletions(-)

# 一个干净的 commit
git commit -m "feat: Electron Desktop App + run.sh installer improvements"

# 推上去
git push -u origin feat/20260304_frontend_improve

# 开发分支继续用，PR 分支提完 PR 后删掉
```

---

## 推荐工作流总结

```
日常开发（碎 commit 随便提）
       │
       ▼
  feat/dev-branch   ← 保留完整历史，继续开发
       │
       │  git merge --squash
       ▼
  pr/clean-branch   ← 1 个干净 commit，用来提 PR
       │
       │  GitHub Merge / Squash Merge
       ▼
     main           ← 干净的主线历史
```

核心原则：**开发分支不碰历史，PR 分支是一次性的干净快照。**
