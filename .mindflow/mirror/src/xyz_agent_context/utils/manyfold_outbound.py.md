---
code_file: src/xyz_agent_context/utils/manyfold_outbound.py
stub: false
last_verified: 2026-08-10
---

# manyfold_outbound.py — managed-reply 声明 + 平台 channel-send 客户端

## 为什么存在

托管开关的**单一事实源**。两个消费方共享同一份声明,防止"字段说
managed、出站却直连"的分裂:

1. `backend/routes/manyfold/sync.py` 的渠道 inventory —— 把声明作为
   `config.agent_managed_reply` **显式布尔**下发给平台(平台 mapper
   对缺失键按 managed-ON 处理,#504 起;显式 false 才能把灰度节奏
   留在我方手里);
2. 渠道模块的出站包装(首个:wechat 的 [[wechat_outbound.py]])——
   按同一声明决定"平台代发 vs 直连 provider"。

## 契约(平台 PR #511,已核对 DTO)

`POST <origin>/internal/narranexus-sync/channel-send`,bearer 与 notify
同 token;body **camelCase**:`runtimeId/agentId/provider/roomId/text/
idempotencyKey(+sourceMessageId/attachments[{path}])`。响应
`status ∈ {sent, queued}` 都算成功(queued = 平台已接单并自己重试
provider 腿)。**请求不带收件人、不带凭据**——目标绑定由平台强制
(无入站历史的 room 403)。

## 设计决策

- 声明 = env `NEXUS_MANAGED_REPLY_PROVIDERS`(逗号列表,缺省空 =
  全 false):灰度是配置变更而非代码变更;
- URL 解析:`MANYFOLD_CHANNEL_SEND_URL` 显式覆盖优先,否则从
  `MANYFOLD_SYNC_WEBHOOK_URL` 尾段 `/notify` → `/channel-send` 派生
  (同一平台 controller 的兄弟路由);派生不出 → 拒绝 managed 路由
  而不是瞎猜,调用方回退直连,是安全退化;
- **never-raise,失败一律 False**——与它替代的 `send_text_once` 同一
  错误面,调用点不因路由不同而分裂错误处理;
- `_transport_for_tests` 模块级测试缝,注入 httpx.MockTransport。

## Gotcha

- `managed_channel_send_active` 要求声明 + env **同时**成立:local/
  自托管无 manyfold env,永远直连——部署面不用关声明也安全。
