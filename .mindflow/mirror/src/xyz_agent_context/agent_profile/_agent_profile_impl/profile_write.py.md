---
code_file: src/xyz_agent_context/agent_profile/_agent_profile_impl/profile_write.py
last_verified: 2026-08-18
stub: false
---

# profile_write.py — 改名事务

> 本文件由 [[_awareness_writes]] 搬来（2026-08-18）。搬迁理由见
> [[_overview]]；下面是事务本身的设计，历史条目在 `_awareness_writes.py.md`。

## 事务包含什么（缺一件就退回事故）

1. 写 `agents` 行（name / description / `extra_updates`），**一次**行写
2. **身份更正**写进 Awareness profile —— 延迟导入，Awareness 不在时降级
3. 刷 `bus_agent_registry`

## 三个反复咬人的区分

**「是不是改名」≠「要不要写」**。存储值未归一化（`"  old  "`）时，归一化后与目标
相等——**不是改名**，但**必须写**，否则那行永远改不了名（谓词比较归一化值，永远
判"已相等"）。`is_rename` 与 `_stored_text_is_unnormalized` 因此是两个判断，
`renamed_from` / `renamed_to` 只跟前者走。

**「写了什么」≠「什么没落地」**。`updated_fields` 与 `unapplied_fields` 分开。合成
一个字段时，含义会随 `ok` 翻转，而下一个做写入埋点的人不会先判 `ok`——**这种错
永不抛异常**，只让指标悄悄偏高。同一类错在这个 PR 里犯了两次（`updated_fields`、
`renamed_to`），第二次是审查抓的。

**「这次调用改没改名」≠「记录对不对」**。这是最贵的一条：笔记原来只在**本次写了
列**时才写，于是**所有已经变异的 agent 都无法修复**——工单里那个 prod agent 行是
「小绿」、记录断言「美食家」，用户把名字改成「小绿」走 no-op 分支，被告知成功而什么
都没变。#320 的形状，低一层。现在义务归**状态**不归写入：记录与行不一致就纠正，
无论这次写没写。`identity_reconciled` 与 `identity_note_recorded` 分开报，
`renamed_*` 保持"这次调用改没改名"的含义。

## 短路是地基，不是优化

`update_agent` 返回 `cursor.rowcount`：SQLite 数 matched、MySQL 数 changed。等值
短路（含 `extra_updates`，第一版漏了、被既有测试当场抓到）保证"没变化就不发写"，
下游才可以**完全不解释那个数字**——落没落地一律以**回读**为准。

## 给后来者

**别再新增 `agents.agent_name` 的写入方**。要写，调本包。护栏命令与 8 行允许清单
在 [[_awareness_writes]]。已知缺口（bundle 导入不刷名录、且原样搬运身份记录）记在
`reference/self_notebook/todo/2026-08-18-bundle-import-identity-gap.md`。
