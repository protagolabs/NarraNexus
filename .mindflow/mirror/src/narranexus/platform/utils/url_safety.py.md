---
code_file: src/narranexus/platform/utils/url_safety.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 消费方扩到全部用户可控的服务端抓取（代码未改，仅记录）

10/28 入侵审计后，`assert_public_http_url` 接进了：provider 测试连接与自定义卡保存
（[[registry]] / [[user_service]]，按卡 source 只门用户卡）、转写 multipart 后端、Home
Assistant 客户端（每次请求前）、NarraMessenger bind（链接 host + guide 里的 homeserver）。
这些接入点**全部仅云端**（铁律 #7），失败一律 fail closed、对用户不回显解析出的地址。

**后续项（刻意未做）：连接 pinning。** 本门是**解析后**校验，但调用方（httpx / aiohttp /
matrix-nio）连接时会**重新解析**主机名——TTL=0 的记录可以在校验与连接之间从公网翻到内网
（DNS-rebinding TOCTOU）。门只缩小窗口，不封死。彻底封死要把连接钉在本函数返回的已校验
IP 上（自定义 transport / resolver，同时保留 TLS SNI 与 Host 头为原主机名）。返回值已是
「已校验 IP 列表」，就是为这一步留的。

## 2026-08-11 — 加 `is_obviously_non_public_url`（parse-safe 存前筛）

`is_obviously_non_public_host` 的整-URL 版:抽 host 后套同一 DNS-free 判定,但**解析失败/非串输入一律判为不安全**(返回 True 而非抛异常)——`urlparse('http://[::1')` 抛 `ValueError`、`urlparse(123)` 抛 `AttributeError`,存前筛(路由 create/update、bundle 导入)绝不能因垃圾输入炸调用方。与连接时门禁的 fail-closed 同一姿态。两个存前调用点(`mcps._blocks_internal_url`、`importer` 写 mcp_urls 前)都改用它,各减一行、语义统一。

## 2026-07-30 — 新增 `is_obviously_non_public_host`（同步、不做 DNS）

第二个函数，回答的是**另一个问题**。`assert_public_http_url` 守的是「服务端自己去
抓一个别人给的 URL」（SSRF，安全边界）；这个守的是「把一个**配置里的** origin 交给
第三方 API 之前，它明显不是公网地址吗」。

首个调用方：[[billing]] 的 `_return_urls`。NetMind 边缘对 loopback/私网 host 的请求
回 HTML 403（任何 scheme），而我们的 client 把 403 映射成 auth 错、路由报 401
「token 无效」—— 所以发出去等于毁掉用户付款还甩锅给他的登录状态。

**刻意不做 DNS**：调用方在付款路由里同步决定发不发，一次 DNS 抖动不该改变这个决定。
因此返回 False 的含义是「不是明显的私网」，**不是**「已确认公网」——这条差别写进了
docstring，免得有人把它当 SSRF 门用。IP 段判定复用既有的 `_is_public_ip`，
知识仍然只有一份。

覆盖的是运维真会手写的那几类：字面私网/回环/link-local IP、`localhost`、
`*.localhost`、`.local`（mDNS）、以及 `my-nas` 这种单标签名（公网永远解析不出来）。

# url_safety.py — SSRF gate for server-side outbound HTTP

## Why it exists

Whenever the SERVER fetches a user/agent-supplied URL it can be tricked into
hitting internal services (SSRF), and on EC2 the metadata endpoint
`169.254.169.254`. `assert_public_http_url` is the single gate every such
fetch must pass. First consumer: the URL-tab embed probe ([[embed_probe.py]]).
Deliberately in `utils/` (not the artifact package) because the planned
headless RenderService and streaming browser (方案三) will reuse it — SSRF is
written once.

## Contract

- Rejects non-http(s) schemes, no-host URLs.
- Literal-IP hosts validated directly (no DNS); hostnames resolved via an
  injectable `Resolver` (real DNS by default, mockable in tests).
- Rejects if ANY resolved address is private / loopback / link-local
  (covers the metadata IP) / reserved / multicast / unspecified — IPv4 and
  IPv6. Validation is POST-resolution, which is what defeats DNS-rebinding.
- Resolution failure / empty resolution is a HARD reject, never a pass.

## Trust boundary gotcha

This guards requests WE originate. An `<iframe src>` is fetched by the
USER's browser, not us, so it is not on this SSRF surface — but open_url
still validates the initial URL here to refuse obviously-internal targets
early. Do not conflate the two.
