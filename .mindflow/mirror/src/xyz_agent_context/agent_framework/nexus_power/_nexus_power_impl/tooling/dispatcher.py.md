---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/dispatcher.py
last_verified: 2026-08-13
stub: false
---

## 2026-08-13（管线审后）— search_lines 封顶不可绕过

三个绕过口全堵（管线审 I#3）：①空白查询先算 tokens、空即走 overview 分支（不再
vacuous 全匹配）；②单 token 查询同受封顶；③工具行与 card 行**分类封顶**（`_SEARCH_MAX_HITS=12` + `_SEARCH_MAX_CARD_HITS=4`，
二审：共享预算会让 12 条工具行饿死整个能力索引）；ALL 路径同样按 token 出现次数
排名后截断（精确命中不再被 scope 序藏掉）。overview 分支刻意不封顶——那就是全量清单请求。
`_hay` 预构建成 dict 复用（原 per-token 重拼）。

## 2026-08-13 — tool_search 多词查询分词匹配

整串子串匹配让多词验证探针（`tool_search("narra reply speak send")`）对在册工具返回
"(no matches)"，模型据此判定回复工具不存在而沉默（8/13 语音对抗实测）。
`search_lines` 改分词：全 token AND 命中优先；AND 空且多词时降级 **ANY token 兜底
——按 token 命中数排名并封顶 `_FALLBACK_MAX_HITS=12`**（胶水词探针不许把整个
工具面灌进 turn，二轮 review Important #4）。card_index 行过滤**镜像产生 hits 的
模式**（AND 命中就 AND，兜底才 ANY）——精确查询不再被松散卡片噪音尾随。
单词查询行为不变。测试锁
tests/nexus_power/test_tooling.py::test_search_lines_multi_word_query_tokenizes。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)(配套:C2 落地)

`visible_tools` 删除每通道 `sorted(name)`——排序会把中途展开的新工具**插进
MCP 段中间**,把插入点之后的字节全部挤出 provider 缓存前缀,正好违反本文件
自己承诺的「expansion appends, never resorts」。现顺序=(通道序,注册序);
注册序确定性由通道自己保证(见 [[mcp_channel]] 同日条目)。工具数组初始顺序
因此一次性变化(旧用户首轮 cache miss 一次,之后照常)。

## 2026-07-30 — 派发前 required 校验(hermes 形)

policy/spec/marker 之后、路由之前,按 spec.input_schema.required 查缺:缺字段
不进 handler,错误里带缺失清单+完整 schema 让模型重发。handler 自己的缺省回退
曾把「参数没到」翻译成误导性错误(write 无 path→workspace 根→Is a directory)。
对 MCP/skills 等所有通道生效(schema 同源)。

# tooling/dispatcher — 唯一分发器

代数缓存(通道 generation 元组)、disallowed/allowlist 过滤在 schema 层生效(A1)、标签工具裁决后短路、search_lines=自研模型无关 tool_search 底座。「模型可见≡实际注册」由本类单点保证。
