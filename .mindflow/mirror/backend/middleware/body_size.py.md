---
code_file: backend/middleware/body_size.py
last_verified: 2026-08-20
stub: false
---

# body_size.py — declared-length 体积门的唯一有效层(#334 r3 I1)

## 为什么存在

FastAPI 在 solve_dependencies **之前**就 `await request.body()` /
`request.form()`(fastapi/routing.py:328-341)——凡带 body field 的
路由,路由级依赖只能在框架缓冲完之后拿到执行权,「快拒」是结构性
假门。HTTP 中间件包在 app 外面,是唯一先于框架碰流的层。

## 契约

- **按路由配上限,绝不做全局单值**:写入口横跨三个数量级(64KB 编辑
  命令 vs 25MB 文档),一个数字必错一边。BODY_CAPS 首匹配生效。
- Content-Length 可撒谎/缺失——这层只是快拒;流式累计第二道在各
  handler(能拿到流的那两个);UploadFile 路由的真实上界=本层+磁盘
  (框架 spool),诚实写在 artifacts.py 注释里。
- **新写入口必须来这里加一行**,漏配=只剩 handler 侧的门(对带
  body field 的路由=没有门)。

## 2026-08-20(二)— 上限单一驻地 + 漂移钉(#334 r4 I2)

MAX_OFFICE_EDIT_BYTES / MAX_OFFICE_ASSET_BYTES / PUT_CONTENT_MARGIN 的
权威在本文件,routes 反向 import(routes→middleware 合法,反向禁止);
MAX_ARTIFACT_BYTES 从 xyz_agent_context.artifact 引入。「门还挂着吗」
不再靠读注释:test_body_size_gate.py 遍历真实路由表验证每条 BODY_CAPS
命中已注册路由 + 中间件确实挂载 + 顺序在 access_log 内。

「**新写入口必须来这里加一行**」这条契约也有执行者了(#334 r5 I2):
`test_every_write_route_has_a_cap_or_an_exemption` 反向遍历——每个已注册
POST/PUT/PATCH 路由要么被某条 BODY_CAPS 命中,要么在测试内的显式豁免
清单 `_NO_BODY_CAP_EXEMPT` 里(按路由模板逐条列,分组注明为何不设上限;
失效豁免同样报红)。新增写端点时 CI 会强制做一次「体积故事」的显式决定。
豁免清单里「小型定形 JSON」一组是否该有平台级默认上限,是一个尚未做的
独立决策——豁免记录的是现状,不是永久背书。
