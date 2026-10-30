---
code_file: src/narranexus/platform/utils/logging/_security.py
last_verified: 2026-10-30
stub: false
---

# utils/logging/_security.py — 越界拒绝的统一日志行

## 为什么存在

跨租户的路径越界在多个独立闸口被拒（agent_id、文件路径、bundle 导入、heal 搜索根），
以前各自用不同措辞记日志或不记，日志侧无法区分"过期链接"和"有人在试边界"。现在选定的
闸口（诚实客户端触发不到的那些，见下文）调 `log_boundary_violation`，行首固定为 `[security.boundary]`，部署侧的日志告警只需
匹配这一个标记。

## 行格式（与部署侧告警的契约，改动需同步）

`[security.boundary] surface=<闸口名> actor=<调用者|-> owner=<记录属主|-> sig=<32hex|-> detail=<repr>`

- **签名**：`sig = HMAC-SHA256(NX_BOUNDARY_LOG_KEY, "surface|actor|owner|detail")` 截前 32 位 hex，
  输入是行里实际打印出的 token，验证方只凭这一行就能重算。日志流里混着平台控制不了的文本
  （异常消息、uvicorn handler、print、子进程），"行头对"证明不了是应用写的，只有签名能。
  哨兵配同一把 key，验签通过才把告警归到账号；未配 key 时 `sig=-`，哨兵不从行里点名任何账号。

- 部署侧从**行首**匹配，连同 loguru 文本格式的行头（时间、级别、
  `..._security:log_boundary_violation:<行号> - `）。改模块名/函数名、包一层 logger、改
  `_setup._TEXT_FORMAT`，告警都会静默失效，必须同步改部署仓 `signatures.py`。JSON 日志模式不支持。
- `surface` 由代码固定，不来自用户输入。当前 6 个：agent_route.agent_id、agent_files.path、
  agent_files.workspace_symlink、artifact.heal_symlink、bundle_import.preflight、
  bundle_import.confirm。
- `tests/utils/test_security_boundary_log.py` 用真实 `_TEXT_FORMAT` 渲染并用哨兵正则的副本
  匹配，契约漂移会变红。
- actor/owner 含空白、`=` 或控制字符时做百分号编码，保证每个字段是一个 token。
- `detail` 通常是攻击者可控的值：用 `repr()` 保证单行（换行被转义，伪造不出第二行），
  截断到 300 字符。真实字段总在前面，告警侧读第一个 `surface=` 即可。

## 什么时候调用

只在诚实客户端不可能触发的地方调用——误报会变成告警。反例：`confine_entry` 不调用，
因为存量数据里有大量遗留布局指针会被它正常拒绝。bundle 结构错误（JSON 形状不对）
也不记，只有真正的路径越界才记。
