---
code_file: scripts/diag_collector/collector.py
stub: false
last_verified: 2026-08-10
---

# collector.py — 诊断日志收集器(push 的接收端)

## 为什么存在

跑在**我方 ops 机**(staging 沙盒→NarraNexus-dev,prod→xyz-algo),
不进任何沙盒。TLS 由宿主机既有 caddy 终结(两台机的 ops-caddy 都已
占 80/443),本服务听内网端口;鉴权 = static bearer
(`DIAG_COLLECT_TOKEN`,与发送端共享)。**服务对服务 HTTP,与
SSH/pem 无关**——pem 仍是人登录看文件用的。

## 存储与保留

`<DATA_DIR>/<env>/<runtime_id>/<service>/<YYYY-MM-DD>.jsonl`,每行
一条自包含 JSON(信封字段合并进行,grep 优先);保留 30 天,启动 +
每日 sweep。路径段白名单清洗(`_segment`):分隔符不可能存活,空段
落 "unknown"——目录结构由不可信的发送端元数据决定,清洗是硬要求。

## 契约

`POST /v1/ingest`(gzip 或明文 ndjson;坏行跳过不沉批;解压后 32MB
上限)→ `{ok, accepted}`;`GET /healthz`。部署:容器 + caddy 一条
反代路由(ops 步骤,在 NarraNexus-deploy 侧)。
