---
code_file: frontend/src/components/settings/NetmindRenewControls.tsx
last_verified: 2026-08-19
stub: false
---

# NetmindRenewControls.tsx — 一次性订阅的「再买 N 个月」

## 为什么单独存在，而不是给订阅按钮加个数字

一次性买断和信用卡订阅是**两个不同的商品**，不是同一个商品的参数。它不会自己续，
所以让用户保持 Pro 的唯一动作就是**在到期前再用一次这个控件** —— 文案因此以
「不会自动续费」开头，并显示**到期日**而不是月数：「3 个月」是抽象的，
「可用至 11 月 18 日」才是能据以行动的。

## 三个不显然的判断

- **信用卡档在这里被隐藏**，而这不是 [[PaymentMethodChoice]] 明确拒绝的那种
  「按地区隐藏」。这是**能力事实**：一次性订阅生效期间，上游对信用卡 subscribe
  回 `400 "Already subscribed to Pro."`（2026-08-19 dev 实测），摆出来就是摆一个必然失败
  的选项。走的是该组件新增的 `hideCard`，注释里写了只有这一种正当理由。
- **月数用格子不用输入框**（1/2/3/6/9/12）。上游边界是 1–12、越界回
  `invalid_months`；用格子之后那个 400 **根本不可达**，而不是"我们再校验一遍"。
- **总价按「一个月多少钱」乘**，不按 `monthly_grant_usd`（一个月给多少额度）。
  今天两者都是 19，正因如此才必须分开：任何一边变了，12 个月的结账会静默算错。
  右侧显式写 `$19.00 × 3` 而不是"省 X%" —— **没有量大折扣，不能暗示有**。

## 反馈是自己的，不是充值的

`state` 传的是**这一笔**的进度（面板用 `payFlow` 判定归属），不是共享的
`rechargeState`。两个控件同处一个弹窗，直接共用会让续订成功后旁边显示
「Top-up complete」。

## 上下游

- 只被 [[NetmindActionZone]] 的 `pro_onetime` 分支消费（经 `renew` slot）。
- 纯展示：购买、防重复提交、轮询全部留在 [[NetmindAccountPanel]] 的
  handler 里，和其余花钱逻辑放在一起。
