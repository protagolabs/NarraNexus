---
code_file: frontend/src/components/settings/NetmindUpsellCard.tsx
last_verified: 2026-07-10
stub: false
---

# NetmindUpsellCard.tsx — Pro 价值主张卡(只在决策时刻出现)

## 为什么存在

$19 订阅与 $19 充值给的 credit **完全等值**(10000:1 换算,xiyue 口径),所以
"订套餐还是充钱"的唯一真差异是**热门模型会员价 + 解锁全模型库**。这张卡的职责
就是在用户被迫做付费选择的那一刻(free × low,由 [[netmindRunway.ts]] 判定)把
这个差异说清——价值 perk 置顶,"每月含 $X 额度"降为末行小字(它和充值等值,
当卖点等于没说)。纯展示组件,subscribe handler 由面板注入。

## 数据来源(决策 A,零写死)

- 价格显示 = `proPlan.monthly_grant_usd`(当前 $19 收 $19 赠额,价格=赠额成立;
  若 NetMind 未来定价与赠额脱钩需改用真价格字段——届时后端补字段)。
- period = `prices[0].period` 经 formatPeriod 透传(dev 是 "2day",显示原样是
  设计行为)——**只出现在标题价格行**;赠额行不带 period(否则渲染成
  "每月含 $19.00 / 月 额度" 的病句,与价格行重复)。
- 会员价 perk 由 `features.member_price !== false` 控制显隐(API 只给布尔)。
  文案 2026-07-10 起按 Owner 决定写"最高 5 折 / up to 50% off"——折扣数字
  **不来自 API**,NetMind 若调整会员折扣需手改 `upsellPerkMember`(en/zh +
  组件内联默认,三处同步)。

## 降级行为(重要)

`proPlan` 为 null(getPlans 失败/列表缺 pro)时**卡照常渲染**,只是价格/赠额
两行隐藏——决策时刻不能因为 plans 接口抖动就没了主 CTA。测试
"plans fetch fails → card still renders" 锁定此行为。

## 上下游

消费方:仅 [[NetmindAccountPanel]](free × low 分支)。plan 形状:
`SubscriptionPlan`(billing.py verbatim 代理 NetMind,无 schema 校验,
渲染全程可选链)。
