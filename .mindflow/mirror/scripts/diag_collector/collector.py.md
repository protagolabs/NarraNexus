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

## 契约与加固(2026-08-10 review 两轮)

`POST /v1/ingest`(gzip 或明文 ndjson;坏行跳过不沉批)→
`{ok, accepted}`;`GET /healthz`。防线从外到内:**流式读请求体,
线上 8MB 边读边封顶**(`request.body()` 会先全量缓冲,5GB 明文可在
任何检查前 OOM)→ gzip **解压过程中** 32MB 封顶(防炸弹)→ 截断/
损坏流 400(EOFError/zlib.error 不是 OSError)。写盘走
`asyncio.to_thread`(大批内联会卡事件循环,把其他 sender 拖过 2s
超时 = 发送端丢批)。鉴权:`hmac.compare_digest`;**无 token 拒绝
启动/请求**,`DIAG_COLLECT_ALLOW_ANONYMOUS=1` 才许裸奔——"开着门"
必须是打过字的决定。分区按 **UTC 日期**(记录自带 UTC ts,本地日期
文件名会在跨零点时与内容自相矛盾)。部署:容器 + caddy 一条反代
路由(ops 步骤,在 NarraNexus-deploy 侧)。
