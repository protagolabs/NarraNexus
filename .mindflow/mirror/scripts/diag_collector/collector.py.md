---
code_file: scripts/diag_collector/collector.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11(七)— 第 8 轮:双边契约补齐 + dev 标签决策(方案 A)

- **决策(Owner 拍板):dev/prod 云栈用 `dev` 标签区分**。两个 EC2
  栈烘焙同一镜像(部署模式都是 cloud),部署模式分不开它们;prod
  保持 `cloud`,**dev 栈在 compose .env 设 `NEXUS_DIAG_ENV=dev`**
  (一台机器一行),即获得独立分区 + 独立 discovery 路由(dev 噪声
  不进 prod 收集器)。`dev` 已进默认词汇表;
- **词汇表是发送端/收集端的双边契约**,现在两边都写了:
  `.env.example` 的 `NEXUS_DIAG_ENV` 与 `DIAG_COLLECT_KNOWN_ENVS`
  条目互相指认("自定义标签必须两边同时加,否则整批降级进最先被删
  的 unknown/"),_ship 模块 docstring 同步;
- **同 env 内合法发送端之间没有隔离**(高音量发送端会挤掉同伴历史)
  ——诚实段补上了良性场景,不只写假冒;并给后人留了升级路径:
  `DIAG_COLLECT_TOKEN` 启用后标签不可假冒,runtime 维度可以安全恢复
  分区权重——token 不只是逃生阀,是两级找平变 sound 的前提;
- **告警 memo 按时间窗重置**(1h):100 个轮换垃圾 label 填满一次
  就永久压制"我方词汇漂移"告警——那正是它存在的唯一理由;现在垃圾
  最多压制一个窗口(跨 worker 无锁 check-then-add 偶尔重复一条,
  已接受并注明);
- 旧行为描述补 "previously" 消歧;`get_deployment_mode` import 挪到
  模块顶;其兜底第二档是 DB-URL 猜测(本地开发指 MySQL 会自标
  cloud)已在 _ship docstring 点明——标签是 best-effort 路由,不是
  强身份。

## 2026-08-11(六)— 第 7 轮:词汇表要和部署路径核对,分区收敛到 env 单维

第 6 轮的白名单默认值和**现网发送端实际发出的标签对不上**:云栈
容器由 compose 直接起(不经 run.sh),`NARRA_SURFACE`/`NEXUS_DIAG_ENV`
都拿不到 → 每条云上记录 env="unknown" → 恰好落进最先被排干的陌生
桶。三处修:

- **发送端兜底改读我方部署契约**(`_ship._env_label` 第三级回落
  `get_deployment_mode()`,云镜像已烘焙 `NARRANEXUS_DEPLOYMENT_MODE`
  =cloud → 零部署改动自标 "cloud";桌面 sidecar 全部注入
  `NARRA_SURFACE=desktop`,原来只有 backend 有);"不嗅探别家集成
  的 env"与"读自家部署契约"是两回事,docstring 已区分;
- **分区键收敛为 env 单维**(`parts[:1]`,对所有 env):假冒已知
  env + 轮换 runtime 曾能铸 257 个子分区做水位找平(反噬阈值
  ~78MB);现在声称某个 env 就**是**那一个分区,轮换一无所获。
  诚实边界:假冒已知 env 的洪水会连我方数据一起从最旧轮转——无
  鉴权不可分辨,逃生阀 `DIAG_COLLECT_TOKEN`;
- **坍缩必须出声**:名单外 label 降级进 unknown/ 时按 label 记
  warning 一次(memo 上限 100 防轮换刷日志)——静默坍缩正是事故
  沉淀 #3/#4 说的"可观测系统自己失效不可检测";白名单条目与来件
  label 过**同一套归一化**(_segment + lower),ops 手滑的空格/大小
  写不再造成全量静默坍缩;写路径的 mkdir/open 重试范围收窄,
  fh.write 移出 try(中途失败重追加会留下"半截+完整"重复内容)。

## 2026-08-11(五)— 第 6 轮:收窄取值域,而不是收窄数量

第 5 轮的"上界 + overflow 坍缩"代入数字仍不成立:16×257 ≈ 4100 个
可铸造分区,水位找平式删除下攻击者让每个分区略小于我方即可。根因
是一直想从攻击者可控的 record 字段里榨出公平性;这轮改为**构造性**
方案:

- **env 白名单**(`DIAG_COLLECT_KNOWN_ENVS`,默认 =
  发送端标签词汇表 staging/cloud/local/desktop):名单外的 env 写入
  时一律坍缩进单一 `unknown/`,且 size-cap 把整棵 `unknown/` 当
  **一个**分区——"陌生流量只能堆在一个分区"不再是猜测而是构造。
  取值域是我们部署侧定义的封闭集合,这是它与"数量上界"的本质区别。
  仍然为真的局限照写:假冒已知 env/runtime 无鉴权不可分辨,逃生阀
  `DIAG_COLLECT_TOKEN`;硬保证仍是封顶 + 全局字节预算;
- **整条 parse 管线挪出事件循环**(`_process_batch` 走 to_thread):
  第 5 轮的目录准入把 stat/iterdir 挂上了事件循环,饱和洪水下
  32MB ≈ 10 万条 × iterdir(256) 足以在限速额度内钉死收集器——所有
  合法发送端一起 2s 超时丢批(第 1 轮"同步写盘"同类问题换门回来,
  铁律 16)。解压/解析/准入/写盘现在整体 off-loop,memo 同时修好
  (已存在目录只 stat 一次、饱和父目录只 iterdir 一次);
- 写路径对 mkdir→open 与空目录 rmdir 的竞态**重试一次**(窗口极小
  但 500 会进发送端熔断账);runtime/service 上界职责收窄为
  **inode 卫生**(overflow 自身占一个名额,真名容量 cap-1);
  `_prune` 恢复泛型标注,三本滑窗账共用同一条修剪规则。

## 2026-08-11(四)— 第 5 轮:分区键是攻击者可控的,策略必须诚实

第 4 轮的"最大分区先删,洪水挤掉自己"在**轮换身份**下不成立且更糟:
分区键(env/runtime)来自请求体字段,每批换 id 就能把洪水摊成无数
小分区,`max(sizes)` 反而选中我方真数据。两层修复:

- **目录数量上界 + overflow 桶**(`_bounded_segment`):每层封顶,
  超限的新名字落 `overflow`(注:"坍缩回同一分区先被排干"的公平性
  结论在第 6 轮被数字推翻并由 env 白名单取代,见上;上界保留但职责
  收窄为 inode 卫生)。归属不丢:每条记录信封字段内联,目录只是索引;
- **诚实声明**:分区删除是**启发式**——自适应攻击者可假冒已知
  env/runtime 落进我方分区,任何基于 record 字段的方案都无法免疫
  (无鉴权即无身份),逃生阀 = `DIAG_COLLECT_TOKEN`。它真正买到的:
  故障的合法发送端刷爆自己的分区时自己被挤出;朴素洪水/轮换洪水
  不再定向删我方数据。硬保证仍是封顶本身 + 全局字节预算。

retention sweep 补**自底向上空目录清理**(unlink 从不删目录,过期/
轮换身份留下的空目录树会让每次 rglob 扫描单调变贵);`_prune` 加
key 参数,三本滑窗账共用同一条修剪规则(改窗口不可能只改到一半)。

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
  文件开始删**到 90% 水位(该策略的适用边界与轮换身份反例见上方
  第 5 轮条目——"洪水先挤掉自己"仅在目录上界 + overflow 桶配合下
  对朴素/轮换洪水成立),写路径按增量节流触发 + 日常 tick 兜底。
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
