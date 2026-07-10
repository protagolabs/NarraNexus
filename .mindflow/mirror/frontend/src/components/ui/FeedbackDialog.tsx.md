---
code_file: frontend/src/components/ui/FeedbackDialog.tsx
last_verified: 2026-07-10
stub: false
---

# FeedbackDialog.tsx — 手动反馈弹窗

## Why it exists

Feedback 机制的显式兜底通道（自动通道是 Agent 的 submit_feedback 工具）。
类别下拉 + 500 字文本框，经 api.submitFeedback 走后端中继。

## 设计决策

- 提交失败也显示感谢页——反馈 UX 绝不能对用户报错（api.submitFeedback 的
  异常被就地吞掉）。
- 类别枚举与服务端 CATEGORIES 对齐，但不含 repeated_failure（那是 Agent
  自察专用类别，用户视角无意义）。
