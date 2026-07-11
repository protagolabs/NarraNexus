---
code_file: frontend/src/components/settings/NetmindTopUpControls.tsx
last_verified: 2026-07-10
stub: false
---

# NetmindTopUpControls.tsx — 充值("Add credits")控件

## 为什么存在

从 [[NetmindAccountPanel]] 抽出的纯展示组件——直接动因是主面板重设计后超过
800 行铁律(853 行),充值 JSX 块(~95 行)是内聚度最高的抽取对象。抽取后
DOM 与文案完全不变,面板测试零改动。

## 职责边界(重要)

**只管渲染**:档位按钮($5/$10/$20/$50,`RECHARGE_TIERS` 常量随迁至此)+
自定义金额输入 + Recharge 按钮 + processing/success/failed 反馈行(含
"Stop waiting" 逃生口)。**不管**:同步双击守卫(rechargeRef)、poll
generation(rechargeGenRef)、Stripe kickoff、金额校验——这些涉及钱的守卫
逻辑全部留在 [[NetmindAccountPanel]] 的 handler 里,通过 props 注入。改充值
行为去面板,改充值长相来这里。

## 上下游

- 消费方:仅 [[NetmindAccountPanel]](`topUp` 变量,三处复用:free 的 manage
  展开区 / free×low 的链接展开 / pro×low 直出)。
- `RechargeState` 类型随迁至此并 export,面板 import 回去。
