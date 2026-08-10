---
code_file: src/xyz_agent_context/module/wechat_module/wechat_outbound.py
stub: false
last_verified: 2026-08-10
---

# wechat_outbound.py — WeChat 出站的单一路由决策点

## 为什么存在

WeChat 的**全部**发送位点(`wechat_send` MCP 工具、模块
`send_to_agent`(ChannelSenderRegistry:step_3 DM 兜底 +
contact_agent)、trigger `send_channel_reply`(managed 错误兜底))
统一改调 `send_wechat_text`,由它按
[[../../utils/manyfold_outbound.py|manyfold_outbound]] 的声明决定:
managed → 平台 channel-send(不需要 context_token,平台按 room_id
解析收件人并自持 iLink 凭据);否则 → 原直连 `send_text_once`,
字节不变。

**新增发送位点必须走这里**——绕过它就会重现"工具走代发、兜底直连"
的双轨分裂,正是本文件要消灭的缺陷类。

## 设计决策

- 平台发送失败**不回退直连重试**:managed 语义 = 平台拥有投递,
  沙盒侧直连补发会与平台自身的重试竞态成双发;
- 路由放模块层而非 `send_text_once` 内部:SDK client 是纯传输层,
  不得知晓 manyfold 语义(分层同 [[wechat_sdk_client.py]])。
