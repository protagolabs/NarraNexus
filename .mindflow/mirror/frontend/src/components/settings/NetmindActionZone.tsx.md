---
code_file: frontend/src/components/settings/NetmindActionZone.tsx
last_verified: 2026-07-10
stub: false
---

# NetmindActionZone.tsx — plan × runway 动作区(至多一个主 CTA)

## 为什么存在

code review 抽取:原 `actionZone()` 是 [[NetmindAccountPanel]] 里 ~100 行的内联
闭包(超 50 行函数守则),且面板当时贴着 800 行上限(784)。抽出后面板降至
~665 行,分支逻辑成为可独立阅读的组件。纯展示:所有带守卫的资金 handler
(subscribe/cancel/reactivate)与 `showManage` 状态仍由面板持有,经 props 注入;
`topUp` 以 ReactNode 传入(其状态守卫在面板)。

## 分支表(与实现一一对应)

| 条件 | 呈现 |
|---|---|
| pro_cancelled(无视 runway) | Resume 主按钮 + Manage balance › |
| free × low | 警示文案 + [[NetmindUpsellCard]] 主推 + 充值降级链接 + 定价外链 |
| pro_active × low | needTopup 文案 + 充值直出(已是 Pro,不再 upsell) |
| free × healthy | 仅 Manage plan & credits ›(展开 = **完整 UpsellCard** + 充值 + 定价外链) |
| pro_active × healthy | 会员价生效提示 + Manage ›(展开才有 Cancel + 充值) |

## 文案门控(review 修复项)

free × low 的警示句按 `freeTierExhausted`(freePct===0,**确知**耗尽)选
`exhaustedChoose`("Free tier used up…");quota 状态未知/禁用时用中性的
`lowChoose`("You're running low…")——**绝不断言未观察到的事实**。

## Owner 走查修复(2026-07-11)

free × healthy 的 Manage 展开区最初放的是**裸 "升级 Pro" 按钮 + 充值档位**——
被 Owner 抓包:这正是重设计要消灭的"两个平级花钱按钮无差异说明"布局,只是
藏深了一层。修复:展开区换成与 low 态**同一张 UpsellCard**(价值主张随 CTA
走,任何出现订阅按钮的地方都必须带"为什么");充值描述加定性区分
(rechargeDesc:"一次性充值,无需订阅")。裸按钮删除后 `subscribeBtn` i18n
key 变死键,同步删除(en/zh)。教训:**价值主张不是某个状态的装饰,是订阅
CTA 的固定伴生物。**

## 上下游

- 消费方:仅 [[NetmindAccountPanel]]。
- `PRICING_URL`(netmind.ai/pricing)随迁至此;`platform.openExternal` 打开。
