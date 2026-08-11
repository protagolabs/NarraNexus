---
code_file: scripts/diag_collector/collector.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11(二)— 安全轮:防线从"识别坏人"改为"按构造封顶自己"

review Critical:旧防线三处不成立——XFF 首段是客户端写的(每请求
换桶)、Content-Length 是声明不是事实、满表 clear() 本身是重置原语。
分层修复:

- **L2 信任跳(第 4 轮修)**:`X-Real-IP` 仅在
  `DIAG_COLLECT_TRUST_REAL_IP=1` 显式声明"我方 caddy 会覆写它"时
  才被读取——默认信任该头等于把身份还给客户端(它也能自己发),
  比 XFF 更隐蔽;漏配的后果是"限速退化成单一全局桶",而非"被静默
  绕过"。**精确 caddy 指令已写进 Tier-1 docstring**(handle_path +
  request_body max_size 8MB + header_up X-Real-IP {remote_host})——
  约束写在散文里没人执行;
- **L3 限速修复**:请求数预检(读体前)+ **实读字节结算**(读完后)
  + 满表 **LRU 淘汰最旧 10%**(不 clear)+ **全局每分钟字节预算**
  (换身份也绕不过的那层);
- **L4 磁盘封顶(公开端点设计的承重墙)**:`DIAG_COLLECT_MAX_DATA_GB`
  (默认 20)——超限时**按分区(env/runtime 前两段)从最大分区的最旧
  文件开始删**到 90% 水位(第 4 轮修:洪水流量先挤掉它自己的分区,
  已知发送端的数据最后才动),写路径按增量节流触发 + 日常 tick 兜底。
  **宿主盘不可能因遥测而满**;诚实的最坏结局:全局预算 256MB/min
  打满时,20GB 缓冲的**保留窗口可被压缩到 ~80 分钟**(20GB ÷
  256MB/min)——文件不丢产品盘,但热数据可被洪水轮转掉;若实践中
  在意,可对 prod 加 `DIAG_COLLECT_TOKEN`(代码已支持)或提高上限;
- L5 经济性(既有):只写不读、响应恒小、无放大——滥用无利可图。

## 2026-08-11 — 公开遥测端点形态(v2)

无 token 即公开是**设计而非疏漏**(前一轮的 fail-closed 被遥测化
重构取代):开源发送端持不了 secret,防线 = per-IP 滑窗限速
(120 req / 64MB 每分钟,内存态,重启即赦)+ 既有体积/炸弹防护;
伪造信封只污染我方诊断数据,换不到权限。`DIAG_COLLECT_TOKEN` 设置
即强制(私有部署旋钮)。新增 `GET /v1/config`:从
`DIAG_COLLECT_CONFIG_JSON` 原样伺服发现文档(只含 URL,公开无鉴权)
——URL 轮换 = prod collector 一处 env 变更,零客户端发版。部署面:
prod = xyz-algo(agent.narra.nexus),staging = NarraNexus-dev
(**dev-agent.narra.nexus,需新增 DNS A 记录**),各一容器 + caddy
`/telemetry/*` 路由;"默认开"的版本发布必须晚于两台部署。

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
超时 = 发送端丢批)。鉴权:`hmac.compare_digest` **按 bytes 比**(str 版对 latin-1 头里
>127 字节抛 TypeError,坏 token 变 500);**无 token 拒绝启动/请求**,
`DIAG_COLLECT_ALLOW_ANONYMOUS=1` 才许裸奔——"开着门"必须是打过字
的决定(Tier-1 docstring 的 Env 清单第二轮才跟上这个语义,并补上了
逃生阀条目;明文路径的 32MB 二次封顶已删——线上 8MB 先行,恒假
分支属铁律 #2 的"以防万一旧路径")。分区按 **UTC 日期**(记录自带 UTC ts,本地日期
文件名会在跨零点时与内容自相矛盾)。部署:容器 + caddy 一条反代
路由(ops 步骤,在 NarraNexus-deploy 侧)。
