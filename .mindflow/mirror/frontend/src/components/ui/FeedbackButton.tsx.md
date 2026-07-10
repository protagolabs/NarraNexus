---
code_file: frontend/src/components/ui/FeedbackButton.tsx
last_verified: 2026-07-10
stub: false
---

# FeedbackButton.tsx — 右下角浮动反馈入口

## Why it exists

Owner 定的位置：反馈按钮悬浮在右下角帮助 "?"（[[HelpButton.tsx]]，
bottom-4）正上方（bottom-14），沿用其圆形+nm-card 视觉语言,形成一个
"求助/反馈"角落。持有 [[FeedbackDialog.tsx]] 的开关状态。desktop only,
挂载在 [[MainLayout.tsx]]。
