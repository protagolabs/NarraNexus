---
code_file: backend/middleware/__init__.py
last_verified: 2026-10-29
stub: false
---
# backend/middleware/ — HTTP 中间件包（及其直接调用的记录器）

每个中间件一个模块，可独立挂载或省略；导入方直接从子模块拿可调用对象，不经包命名空间，
保持依赖图显式。

不是每个模块都是 ASGI 中间件：`login_ips` 是记录器——auth 中间件、以及不经任何 HTTP
中间件的 WebSocket 鉴权，都调用它记下用户的公网客户端 IP（滥用 IP↔账号联动）。放在这里
而不是另立目录，是因为它的调用方就是鉴权这一层；包 docstring 写明了这一点，免得下一个
读者以为本包里全是中间件、去别处找 `instance_login_ips` 的写入方。
