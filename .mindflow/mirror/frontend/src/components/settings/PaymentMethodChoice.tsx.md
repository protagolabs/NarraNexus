---
code_file: frontend/src/components/settings/PaymentMethodChoice.tsx
last_verified: 2026-08-18
stub: false
---

# PaymentMethodChoice.tsx — 信用卡 / 支付宝 / 微信 三选一

## 为什么存在

接入 nexus Stripe 账号后，**充值和订阅两条流程都要选支付方式**，而两边的选项
文案、图标、交互完全一样。抽出来是为了「加一个支付方式只改一个地方」，不是为了
凑组件数。

## 为什么是 radiogroup 而不是三个按钮

这是互斥选择：方向键必须能在选项间移动，且整组**只占一个 tab 位**（roving
tabindex）。三个 `<button>` 看起来一模一样，但对不用鼠标的人行为是错的 —— 会被
挨个 tab 过去，且方向键什么也不做。

## 为什么「卡」那一档的 value 是 prop

上游对同一条支付通道在两个接口里拼写不同：充值叫 `default`，订阅叫 `stripe`；
而支付宝和微信两边同名。把**唯一不同的那个值**作为 `cardValue` 传进来，比让每个
调用方各自维护一份完整选项列表要少一处漂移点。

## 刻意不做的事

- **不按地区隐藏任何一档**。谁能用哪种支付方式是用户自己的事 —— 在中国用境外卡、
  或在境外用支付宝，都不是值得为它悄悄砍掉一个选项的边缘情况。
- **不按语言自动选中**（比如中文默认支付宝）。这是可以做的，但它是**偏好**不是
  **能力**，而且需要读 i18n 的当前语言；本期先让卡（唯一不需要解释、不需要换算的
  那档）当默认。记在这里，免得下次有人以为是漏了。

## 上下游

- 消费方：[[NetmindTopUpControls]]（`cardValue="default"`）。订阅侧接入时用
  `cardValue="stripe"`。
- 文案键 `settings.netmind.pay*`，受 [[netmindI18nDefaults.test]] 的漂移检查覆盖
  （该测试的文件清单在本次一并补上了本组件与 [[NetmindTopUpControls]]）。
