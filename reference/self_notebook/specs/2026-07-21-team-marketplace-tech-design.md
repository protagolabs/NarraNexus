# NarraNexus Team Marketplace — 技术设计

版本: v0.1 · 日期: 2026-07-21 · 状态: Design(待 Hongyi 确认 §2 决策点)
关联: `2026-07-20-skill-marketplace-tech-design-v1.1.md`(共享命名空间 / artifact store)
关联: `2026-07-16-in-app-marketplace-design.md`(未合并分支 `ee1db871` 的原始设计)

---

## 〇、一句话

Team Marketplace 上架「团队/Agent 模板」(`.nxbundle`),用户浏览后一键
fork 成自己账号下的一套 agents。**几乎全部资产已在未合并分支 `ee1db871`
里写好**(catalog 表 / 路由 / 9 个 seed / 前端页),本设计做三件事:① 挂到
预留的 `/api/marketplace/teams/*` 前缀;② blob 从 narra.nexus 静态托管改为
**我们自己的 artifact store(S3 / 本地,与 skill 分开)**;③ 前端与 Skill
拼成「一个 Marketplace,两个 tab」。安装引擎零新增——复用现有 bundle
importer。

---

## 一、复用清单(来自 `ee1db871` + 现有代码,直接搬)

| 资产 | 来源 | 复用方式 |
|------|------|----------|
| `marketplace_templates` 表 | ee1db871 schema_registry | 改名 `team_catalog` 对齐 skill_catalog 命名;字段基本照搬 |
| `MarketplaceTemplate` schema | ee1db871 | → `TeamTemplate`,`bundle_url` 换成 `store_key`(见 §3) |
| `marketplace_template_repository.py` | ee1db871 | → `team_catalog_repository.py`,list_enabled/list_all/upsert |
| `_marketplace_seed.py`(9 template) | ee1db871 | 迁移逻辑复用;bundle 从 narra.nexus 拉一次、重新上架到我们 store |
| **bundle importer**(preflight/confirm,fork 语义) | 现有 `bundle/importer.py` | **零改动**——已支持多 agent + team 的 id remap |
| **artifact_store.py**(S3/本地抽象,boto3 仅此一处) | skill marketplace | 加 `get_template_store()`,独立 prefix |
| 前端 bundle 导入 UI(preflight review → confirm) | 现有 `BundleImportPage` | 复用;Team 安装复用同一 review 组件 |
| staff gate(`_require_staff_or_raise`) | ee1db871 / admin_quota | 上架/删除端点复用 |

**关键事实**:`.nxbundle` = zip(manifest.json + 每个 agent 目录 + workspace.tar.gz)。
「team 模板」= 带 `team_id` 的多 agent bundle;单 agent 模板就是不带 team 块的
同一格式。`agent_count` 只是卡片徽标,schema 无区别。importer.confirm() 全程
mint 新 id 落库到当前用户名下(fork,装完与模板解耦、无自动更新)——这正是我们
要的语义。

---

## 二、待确认的设计决策(Hongyi）

### 决策 1 — 安装路径与部署模式(★已厘清,复用 skill 的 source 抽象)

**核心物理约束**:安装 = fork 到「用户自己的库」。DMG/本地版落本机 sqlite,
线上版落云端 RDS——所以 `importer.preflight()`/`confirm()` **必须在本地后端
执行**。而 catalog + `.nxbundle` blob 在云端(一份权威目录 + 云端 S3);
**DMG 本机没有 S3 凭证**(同它连不了 RDS)。这两条把路径锁死为:

```
                     取 bundle 字节              preflight + confirm
线上用户   本后端就是 registry → 直读 store        本地 importer → RDS
DMG 用户   本后端是客户端 → HTTP 下载云端 blob      本地 importer → sqlite
              ↑ 两模式在此分叉(被架构逼的)          ↑ 两模式完全同一条路径
```

**结论:不是全局 A-or-B,而是复用已建好的 source 抽象**(skill 侧的
`LocalMarketplaceSource` 直读 store / `RemoteMarketplaceSource` HTTP 拉云端,
已跑通已测)。Team 侧镜像:

```
POST /api/marketplace/teams/templates/{id}/install-preflight   (本地后端)
    path = source.resolve_bundle(template_id)      # 唯一分叉点
        ├─ registry host(线上）→ store.get_to_path(...)         # 不 HTTP 自调
        └─ client(DMG)        → GET 云端 /.../download → temp    # 必要的跨机 fetch
    校验 sha256 → importer.preflight(path, user_id) → {token, manifest, clashes}
confirm: 复用现有 POST /api/bundle/import/confirm(本地跑)
```

要点:① preflight/confirm **两模式同一路径**,满足铁律 #7;② 分叉只在"取字节"
一步,且是物理必然(DMG 无 S3 凭证 / fork 必须落本地库),非设计选择;
③ 线上后端**直读自己的 store**,不做「HTTP 请求自己」的绕路(这才是原 A 方案
的缺点);④ registry host 仍需一个
`GET /templates/{id}/download`(服务 blob,DMG 从这拉、验 sha256 header),
这是 store 的唯一对外出口。

原 `ee1db871` 走 narra.nexus 静态 + `/import/from-url` 后端自拉的方案**弃用**
——统一走我们 artifact store + source 抽象。

### 决策 2 — 前端信息架构

你的话:「左边 marketplace 里有 skill 和 team 两个地方」。两种落法:
- **方案 T1(推荐)**:`/app/marketplace` 页顶部加 tab「Skills | Teams」,
  一个侧边栏入口,内部切换。
- 方案 T2:侧边栏 Marketplace 展开二级菜单,两个独立路由
  `/app/marketplace/skills`、`/app/marketplace/teams`。

**AI 建议:T1**,一个入口 + 页内 tab,改动最小、心智最简;当前
`MarketplacePage` 已是 Skill 页,加一层 tab shell 即可。

### 决策 3 — 上架范围(MVP)

- **AI 建议**:MVP 只做**官方 template**——把 `ee1db871` seed 里的 9 个
  (financial-morning-briefing / marketing-team / gaokao-team 等)从
  narra.nexus 拉下、重新上架进我们 store;社区上传推后。上架用一个
  staff-gated 端点 + CLI(镜像 skill 的 publish_skill.py)。

---

## 三、数据库设计(schema_registry,双方言,additive)

`team_catalog`(对齐 skill_catalog 命名与风格):

```python
_register(TableDef(
    name="team_catalog",
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("template_id", "TEXT", "VARCHAR(128)", nullable=False),   # stable slug
        Column("name", "TEXT", "VARCHAR(255)", nullable=False),
        Column("description", "TEXT", "TEXT"),
        Column("categories_json", "TEXT", "TEXT"),                       # ["finance","team"]
        Column("author", "TEXT", "VARCHAR(128)"),
        Column("agent_count", "INTEGER", "INT", nullable=False, default="1"),
        Column("thumbnail_url", "TEXT", "VARCHAR(1024)"),
        Column("store_key", "TEXT", "VARCHAR(512)", nullable=False),     # artifact store key (was bundle_url)
        Column("bundle_sha256", "TEXT", "VARCHAR(80)", nullable=False),
        Column("enabled", "INTEGER", "TINYINT(1)", nullable=False, default="1"),
        Column("sort_order", "INTEGER", "INT", nullable=False, default="0"),
        Column("downloads", "INTEGER", "BIGINT", nullable=False, default="0"),
        Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[
        Index("idx_team_catalog_tid", ["template_id"], unique=True),
        Index("idx_team_catalog_enabled_sort", ["enabled", "sort_order"]),
    ],
))
```

与 `ee1db871` 的差异:`bundle_url`(外部 URL)→ `store_key`(我们 artifact
store 的 key);加 `downloads` 计数与 skill 对齐。**无「installations 审计表」**
——安装即 fork 到 `agents`/`teams` 表,那才是真相;team_members.source 已带
`bundle:<id>` 溯源(现有约定)。

---

## 四、Artifact Store(S3 / 本地,与 skill 分开)

复用 `_skill_marketplace_impl/artifact_store.py`,新增 `get_template_store()`:

- **S3**:`TEMPLATE_S3_BUCKET`(未设则回落 `SKILL_S3_BUCKET` 同 bucket)
  + `TEMPLATE_S3_PREFIX`(默认 `narranexus-teams`)。与 skill 的
  `narranexus-skills/` 前缀**物理分开**。
- **本地**:`<base_working_path>/../marketplace_store/teams/`(skill 在
  `marketplace_store/`,team 落其下的 `teams/` 子目录——同你说的「文件夹分开」)。
- store key 约定:`{template_id}/{version_or_sha8}/{template_id}.nxbundle`。

boto3 仍只在 artifact_store.py 一处(铁律 #9)。

---

## 五、后端 API(`/api/marketplace/teams/*`)

新增 `backend/routes/marketplace_teams.py`,挂 `prefix="/api/marketplace/teams"`
(main.py 已注释预留此前缀)。端点(移植 ee1db871 + 决策 1 方案 B):

| Method & Path | 说明 | Auth |
|---|---|---|
| `GET /templates` | 上架列表(enabled,按 sort_order);`?agent_id` 无意义(fork,不注入 installed) | 登录(读面沿用 skill 的可选认证亦可) |
| `GET /templates/{id}` | 详情 = 卡片元数据 + agent_count + categories | 登录 |
| `GET /templates/{id}/download` | registry host 从 store 服务 `.nxbundle`(带 sha256 header);DMG 客户端从这里跨机拉 | 登录（读面） |
| `POST /templates/{id}/install-preflight` | 经 source 抽象取 bundle(线上直读 store / DMG 拉 download)→ 验 sha256 → **本地** importer.preflight → 返回 preflight_token + manifest + clashes(与 /import/preflight 同 shape) | 登录 |
| `POST /templates` | 上架/更新(staff);multipart .nxbundle → 存 store → 写 catalog | **staff** |
| `DELETE /templates/{id}` | 下架(staff) | **staff** |
| `POST /seed` | 迁移 9 个官方 template(staff,幂等) | **staff** |

confirm 复用现成 `POST /api/bundle/import/confirm`(不新增)。install-preflight
成功后 `downloads += 1`(或在 confirm 回调里加,二选一,倾向 preflight 时加)。

service 层 `team_marketplace_service.py`(镜像 skill_marketplace_service):
cloud=本地 DB registry;desktop=proxy cloud API(读 + install-preflight 都可
走 cloud;confirm 在本地 importer 执行,fork 落本地/云端各自库)。

---

## 六、前端(一个 Marketplace,两个 tab — 决策 2 方案 T1)

- `MarketplacePage.tsx` 提升为 shell:顶部 `[ Skills | Teams ]` tab。
  - Skills tab = 现有内容(搬进子组件 `SkillMarketplaceTab`)。
  - Teams tab = 新 `TeamMarketplaceTab`:卡片(名称、`agent_count` 徽标、
    categories chip、缩略图)+ 分类筛选 + 详情 modal。
- 安装:点 Install → 调 `install-preflight` → 复用现有 bundle preflight
  review UI(展示将创建哪些 agent / 是否重名)→ confirm → 跳到新 agent。
  前端 `importBundleFromUrl` 已存在;若走方案 B 则新增一个
  `installTeamTemplate(id)` thunk 调 install-preflight。
- 复用 `ee1db871` 的 `marketplaceStore` / `types/marketplace.ts` 骨架,改指
  Teams 端点。
- i18n:`sidebar.marketplace` 已有;新增 tab 文案 `marketplace.tabs.skills/teams`。

---

## 七、实施分段(结构维度,铁律 #17)

| 段 | 内容 | 依赖 |
|----|------|------|
| T1 | `team_catalog` 表 + repository + `TeamTemplate` schema | — |
| T2 | `get_template_store()`(artifact_store 扩展,独立 prefix) | — |
| T3 | `marketplace_teams.py` 路由 + `team_marketplace_service.py`(含 install-preflight→importer.preflight) | T1/T2 + 现有 importer |
| T4 | 上架 CLI(`publish_team_template.py`)+ seed 迁移(9 官方 template 从 narra.nexus 拉→重上架) | T3 |
| T5 | 前端 Marketplace tab shell + TeamMarketplaceTab + 安装接线 | T3 |
| T6 | 端到端验收(本地:上架→浏览→install-preflight→confirm→新 team 落库)+ 双模式 | 全部 |

风险等级:低——安装引擎(最复杂、最易错的部分)零改动复用;新增全是 catalog +
路由 + 前端,与 skill 侧同构、独立可回滚。

---

## 八、明确不做 / 推迟

- 社区上传 team template(MVP 只官方);模板评分/评论;安装后自动更新
  (fork 语义,永不自动更新——设计如此);converter/批量生产 pipeline
  (上游独立);多 pod 对象存储化 bundle_preflight(已有 scaling_assumptions
  记录,与本项目解耦)。
- narra.nexus 静态托管**弃用**——统一走我们 artifact store(你的要求)。
