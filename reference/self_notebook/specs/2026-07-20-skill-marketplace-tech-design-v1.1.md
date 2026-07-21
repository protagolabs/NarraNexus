# NarraNexus Skill Marketplace — 技术设计 v1.1(修订版)

版本: v1.1 · 日期: 2026-07-20 · 状态: Approved-for-implementation
基于: Phase 3 PRD v0.2 + Phase 4 v1.0,经代码调研修订
修订决策人: Hongyi(1/2/3 号问题)+ AI 建议采纳(4 号问题)

---

## 〇、v1.1 相对 Phase 4 v1.0 的修订记录

| # | v1.0 方案 | v1.1 修订 | 理由 |
|---|-----------|-----------|------|
| 1 | PostgreSQL(JSONB/GIN/tsvector)+ alembic + Celery + Redis | **复用现有栈**:`schema_registry.py` 双方言表 + `auto_migrate()` + `services/` poller 模式,不引入任何新基础设施 | 代码库无 PG 方言、无 alembic;后台任务已有 poller 模式;Skill < 100 时 MySQL LIKE + JSON 查询完全够用 |
| 2 | 独立 marketplace/ 微服务(~18 files) | **收敛为核心包内的 service + impl 层**;Marketplace 是统一命名空间、按对象拆两个子前缀:`/api/marketplace/skills/*`(本项目:skill 探索/安装)与 `/api/marketplace/teams/*`(预留:agent/team bundle 分享,未来承接 `feat/in-app-marketplace` 的能力) | 两类 marketplace 共享一个心智入口但对象不同;子前缀隔离让两者可独立演进、互不阻塞 |
| 3 | `skill_installations` 表与磁盘双真相 | **明确"磁盘唯一真相,DB 是审计跟随者"** + 三道防线同步机制(见 §5) | 无法完全控制用户行为,必须有对账兜底 |
| 4 | `env_config` base64(沿用现状) | **MVP 升级为 Fernet 真加密**,惰性迁移旧值(见 §8) | Marketplace 放大第三方 skill 收集凭证的场景;symlink 逃逸 Gap 未修复前不能裸奔 |
| 5 | S3 `registry-index.json` 中央索引文件 + Agent 定时拉取 | **删除 index 文件,DB catalog 是唯一目录真相**;更新检查改为 `GET /api/skills/updates` 批量接口 | 消除 v1.0 自己列的"风险三:Registry Index 一致性"——只有一个目录源就没有 Index/DB 漂移 |
| 6 | 人天/周排期(~71d/14 周) | **结构维度评估**(见 §11) | CLAUDE.md 铁律 #17 |
| 7 | 安全三层隔离绑定 Codex CLI | 静态扫描 Gate 定位为**框架无关的主防线**;Codex TOML 是当前 runtime backend 的实现之一(见 §7) | 铁律 #9:代码库同时存在 `xyz_claude_agent_sdk.py` 路径 |
| 8 | `.skill_meta.json` "新增" source/source_url/installed_at | 修正:这三个字段**已存在**(`source_type`/`source_url`/`installed_at`),真正新增只有 `hash`、`updated_at`;统一沿用现有命名 `source_type` | 代码事实(`_save_skill_meta`) |

不变的部分(直接继承 Phase 3/4):SKILL.md + manifest.json 双文件规范、S3 存 artifact、7 步 Install Engine、静态扫描规则集、Known Gaps 清单、"Agent Runtime 完全不动"原则、默认通知不自动更新、Prompt 驱动安装直接替换 + config 迁移。

---

## 一、总体架构

```
┌─ Desktop DMG / bash run.sh ─────────────┐   ┌─ Cloud (agent.narra.nexus) ────────────┐
│ Frontend SkillsPanel + Marketplace UI    │   │ Frontend(同一套代码)                   │
│        │                                 │   │        │                               │
│ backend/routes/skills.py (本地 FastAPI)  │   │ backend/routes/skills.py(同一套代码)  │
│        │ marketplace 子路由 ────────────────HTTPS──→ 同一 API,cloud 实例是 Registry  │
│        ▼                                 │   │        ▼         的权威节点            │
│ SkillMarketplaceService                  │   │ SkillMarketplaceService                │
│   ├─ InstallPipeline(7 步)              │   │   ├─ InstallPipeline(7 步)            │
│   ├─ RegistryClient ──→ 指向 cloud API   │   │   ├─ RegistryService(本地 DB 即目录) │
│   └─ Scanner(安装前本地复扫)           │   │   └─ Scanner(发布 Gate)              │
│        ▼                                 │   │        ▼                               │
│ ~/.nexusagent/.../skills/ (磁盘真相)     │   │ workspaces 卷 skills/ (磁盘真相)       │
│ sqlite: skill_installations (审计)       │   │ MySQL: skill_catalog + installations   │
└──────────────────────────────────────────┘   │ S3: artifact (zip + manifest.json)     │
                                               └────────────────────────────────────────┘
```

关键定位:

- **Registry(目录)只有一份权威**:cloud MySQL 的 `skill_catalog` 表。桌面端不承载目录,只作为 client 调 cloud API。
- **Install Engine 跟着 Agent 走**:桌面装进本机磁盘,cloud agent 装进服务器 workspace 卷。装完的磁盘结构与手写 skill 完全一致,Agent Runtime 零感知、零改动。
- **S3 只存"货"**(每版本 zip + manifest.json),不存目录。artifact 不可变:同一 `skill_id@version` 的 S3 对象发布后永不覆盖,改内容必须发新版本号。
- **桌面离线降级**:Registry 不可达时,Marketplace 浏览/搜索/更新检查显示不可用提示;本地已装 skill、zip/GitHub/URL 安装路径完全不受影响。

依赖方向(单向,符合现有分层):

```
backend/routes/skills.py
  → skill_marketplace_service.py(service 协议层)
    → _skill_marketplace_impl/(registry.py, install_pipeline.py, scanner/)
      → SkillModule 现有 API(install_skill / remove_skill / _parse_skill_md / _save_skill_meta)
      → repository/(catalog / installation / scan_result)
        → AsyncDatabaseClient + schema_registry
```

---

## 二、Skill 包格式 v1.0(冻结,继承 Phase 3 §3)

目录结构、SKILL.md 规范、manifest.json schema 与 Phase 3 §3.1–3.4 完全一致,此处只记录两处落地澄清:

1. **manifest.json 是 Marketplace 维度的补充元数据**,安装引擎以 manifest.json 为权威源、SKILL.md frontmatter 为 fallback(Phase 3 已定)。URL/GitHub 安装的裸 skill 没有 manifest.json 时,由 `_parse_skill_md` 结果自动合成最小 manifest(id=name 的 kebab-case、version 缺省 `0.0.0`、capabilities 空)。
2. **`requires.env` / `requires.binaries` 沿用现有 clawdbot 兼容解析**(`_parse_skill_md` 已支持),manifest 的 `config_schema` 是它的超集;两者都在,以 `config_schema` 为准。

包格式支持(继承 Phase 3 §3.5):`.zip`(P0,Marketplace + URL)、Git 仓库 URL(P1,代码已有)、裸 SKILL.md URL(P2)、内置 builtin(已有)。

---

## 三、数据库设计(修订:schema_registry 双方言)

4 张新表全部通过 `utils/schema_registry.py` 的 `_register(TableDef(...))` 注册,`auto_migrate()` 幂等建表;**现有表零改动**(铁律 #6)。JSON 载荷统一用 `TEXT` / `MEDIUMTEXT` 存序列化 JSON——与现有 `instance_lark_bindings.config_json` 等先例一致,不依赖 MySQL JSON 类型的方言特性。

表在两种部署模式下都会创建(auto_migrate 无条件跑),但 `skill_catalog` / `skill_scan_results` 只有 cloud 实例写入;桌面端这两张表为空表,无副作用。

### 3.1 skill_catalog(目录核心表,cloud 权威)

```python
_register(TableDef(
    name="skill_catalog",
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),      # kebab-case
        Column("version", "TEXT", "VARCHAR(32)", nullable=False),        # semver
        Column("name", "TEXT", "VARCHAR(255)", nullable=False),
        Column("description", "TEXT", "TEXT"),
        Column("author_json", "TEXT", "TEXT"),                           # {name, email}
        Column("license", "TEXT", "VARCHAR(64)"),
        Column("category", "TEXT", "VARCHAR(32)"),                       # fallback|utility|integration|enhancement
        Column("capabilities_json", "TEXT", "TEXT"),                     # ["search:web", ...]
        Column("tags_json", "TEXT", "TEXT"),
        Column("config_schema_json", "TEXT", "MEDIUMTEXT"),
        Column("dependencies_json", "TEXT", "TEXT"),                     # {skill_id: version_range}
        Column("compatibility_json", "TEXT", "TEXT"),                    # {narranexus_min, narranexus_max}
        Column("s3_key", "TEXT", "VARCHAR(512)", nullable=False),        # artifact 位置
        Column("package_hash", "TEXT", "VARCHAR(80)", nullable=False),   # sha256:...
        Column("publisher", "TEXT", "VARCHAR(128)"),
        Column("scan_status", "TEXT", "VARCHAR(16)", nullable=False),    # passed|warning|rejected
        Column("status", "TEXT", "VARCHAR(16)", nullable=False),         # published|deprecated|unlisted
        Column("downloads", "INTEGER", "BIGINT", nullable=False, default="0"),
        Column("avg_rating", "REAL", "DECIMAL(3,2)"),                    # 预留,MVP 不写
        Column("published_at", "TEXT", "DATETIME(6)"),
        Column("created_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
        Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[
        Index("idx_skill_catalog_id_ver", ["skill_id", "version"], unique=True),
        Index("idx_skill_catalog_category", ["category"]),
        Index("idx_skill_catalog_status", ["status"]),
    ],
))
```

搜索实现:`name/description LIKE %q%` + category 精确过滤 + capabilities/tags 用 `LIKE '%"search:web"%'` JSON 子串匹配(序列化时保证紧凑无空格,匹配可靠)。排序 downloads/published_at/name。Skill 量级 < 100,这套够用;升级 ES/向量搜索是后续独立决策。

### 3.2 skill_installations(安装审计,双端都写)

```python
_register(TableDef(
    name="skill_installations",
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
        Column("user_id", "TEXT", "VARCHAR(64)", nullable=False),
        Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),      # == 目录名
        Column("version", "TEXT", "VARCHAR(32)"),
        Column("source_type", "TEXT", "VARCHAR(16)", nullable=False),    # marketplace|url|github|zip|builtin|manual
        Column("source_url", "TEXT", "VARCHAR(1024)"),
        Column("package_hash", "TEXT", "VARCHAR(80)"),
        Column("status", "TEXT", "VARCHAR(24)", nullable=False),         # installed|uninstalled|external_removed|modified|disabled
        Column("last_event", "TEXT", "VARCHAR(24)"),                     # install|update|rollback|uninstall|reconcile
        Column("installed_at", "TEXT", "DATETIME(6)"),
        Column("updated_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[
        Index("idx_skill_inst_agent_user_skill", ["agent_id", "user_id", "skill_id"], unique=True),
        Index("idx_skill_inst_user", ["user_id"]),
    ],
))
```

要点:唯一键是 **(agent_id, user_id, skill_id)** 三元组——workspace 本身就是 `{agent_id}_{user_id}` 维度,v1.0 的 UNIQUE(agent_id, skill_id) 是错的。`source_type` 枚举与 `.skill_meta.json` 现有字段同名同值,新增 `manual`(对账器发现的手工安装)。

### 3.3 skill_scan_results(扫描审计,cloud 写)

```python
_register(TableDef(
    name="skill_scan_results",
    columns=[
        Column("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
        Column("skill_id", "TEXT", "VARCHAR(128)", nullable=False),
        Column("version", "TEXT", "VARCHAR(32)", nullable=False),
        Column("status", "TEXT", "VARCHAR(16)", nullable=False),         # passed|warning|rejected
        Column("high_issues", "INTEGER", "INT", nullable=False, default="0"),
        Column("low_issues", "INTEGER", "INT", nullable=False, default="0"),
        Column("issues_json", "TEXT", "MEDIUMTEXT"),
        Column("scanner_version", "TEXT", "VARCHAR(16)"),
        Column("scanned_at", "TEXT", "DATETIME(6)", nullable=False, default="(datetime('now'))"),
    ],
    indexes=[Index("idx_skill_scan_id_ver", ["skill_id", "version"])],
))
```

非唯一索引:同一版本可多次重扫(scanner 规则升级后回扫),取 `scanned_at` 最新一条为准。

### 3.4 team_skill_policies(占位注册,MVP 不实现逻辑)

结构继承 Phase 4(team_id, skill_id, policy_type: recommended|allowed|blocked)。表先注册(避免后续加表的迁移成本),路由与 UI 全部推迟到 Team Recommended 阶段。

### 3.5 Repository

- `repository/skill_catalog_repository.py` — `SkillCatalogRepository(BaseRepository)`:`search()`, `get_latest()`, `get_version()`, `list_versions()`, `increment_downloads()`
- `repository/skill_installation_repository.py` — `upsert_event()`, `list_for_workspace()`, `mark_status()`
- `repository/skill_scan_result_repository.py` — `latest_for()`, `record()`

Schema(Pydantic)集中在 `schema/skill_marketplace_schema.py`:`SkillCatalogEntry`, `SkillInstallationRecord`, `SkillScanResult`, `SkillSearchQuery`, `SkillSearchPage`。

---

## 四、Registry Service(cloud 侧)

职责收敛为三件事(v1.0 的 S3 index 同步/Celery 重建全部删除):

1. **Search / Detail / Updates 查询** — 直接查 `skill_catalog`,无缓存层(量级不需要;将来加也是进程内 TTL dict,不引 Redis)。
2. **Publish 流水线**(MVP 仅限官方/内部发布,无社区上传):
   校验 manifest → Scanner 静态扫描(§7)→ HIGH 拒绝(422 + scan_report)/ LOW 警告放行 → zip 上传 S3(`narranexus-skills/{skill_id}/{version}/{skill_id}-{version}.zip` + 同目录 manifest.json)→ 写 `skill_catalog` + `skill_scan_results`。全流程同步执行(单个包扫描秒级,不需要异步任务队列)。
3. **下载计数** — install 成功回调时 `increment_downloads()`。

S3 访问封装在 `_skill_marketplace_impl/artifact_store.py`,接口按对象存储抽象(`put/get/presign`),boto3 只在这一个文件出现——铁律 #9,将来换 R2/OSS 只动这里。桌面端不触碰此文件(下载走 presigned URL 或 API 代理,见 §6 Install API)。

---

## 五、Install Engine 与 磁盘↔DB 一致性(核心修订)

### 5.1 真相模型

**磁盘(`skills/` 目录 + `.skill_meta.json`)是唯一真相;`skill_installations` 是审计投影,永远跟随磁盘,冲突时磁盘赢。** 对账器只写 DB,永不创建/删除/修改用户磁盘上的任何文件。

### 5.2 三道防线

**防线 1 — 唯一写路径。** 所有安装/卸载/更新/回滚入口(UI 按钮、URL 安装、Agent MCP 工具、未来 Team Recommended)全部收敛到 `InstallPipeline`。Pipeline 在同一次调用里完成:磁盘操作 → `.skill_meta.json` 更新 → `skill_installations` upsert → `backup_after_api_install()`。DB 写失败不回滚磁盘(磁盘是真相),记 error log 等对账器补齐。

**防线 2 — Prompt 约束。** `SKILL_INSTRUCTIONS_TEMPLATE` 的 `WORKSPACE_RULES_CLOUD/LOCAL` 增加一条(双模式同文,铁律 #7):

> Never create, delete, or modify skill directories under `skills/` by hand (no `mkdir`/`rm`/`cp` on skill folders). To install, uninstall, or update a skill, always use the dedicated skill tools (`skill_install`, `skill_uninstall`, `skill_search_marketplace`). Hand-edited skill state will be flagged as unmanaged.

**防线 3 — 对账器(reconciler)。** 新增 `services/skill_sync_service.py`,复用 ModulePoller/InstanceSyncService 的后台 poller 模式:

- **触发时机**:进程启动一次 + 定时(默认 30min,可配)+ 每次 InstallPipeline 完成后对该 workspace 增量对账一次。
- **对账逻辑**(对每个 workspace 的 `skills/`,含 `.disabled/`):
  | 磁盘 | DB | 动作 |
  |------|----|------|
  | 有 | 无 | 补 upsert:`source_type=manual`(`.skill_meta.json` 有 source 信息则用它),`last_event=reconcile` |
  | 无 | `status=installed` | 改 `status=external_removed`(不删行——审计要留痕) |
  | 有,hash ≠ DB hash | 有 | 改 `status=modified`(用户改过内容;UI 显示 unmanaged 标记) |
  | `.disabled/` 下 | `status=installed` | 改 `status=disabled` |
- **幂等**:全部是"算目标状态然后 upsert",跑多少遍结果一致。
- **审计事件**:对账器自身的 started/finished/error 走现有 lifecycle audit 约定(工程教训 #5)。

hash 计算:安装时对 zip 算 sha256 存入 `.skill_meta.json` 新增的 `hash` 字段;对账时对目录做确定性内容 hash(排序后逐文件 sha256,排除 `.skill_meta.json` 自身)。两者分开存(`hash` = 包 hash,`content_hash` = 目录 hash),`modified` 判定用 `content_hash`。

### 5.3 7 步 Pipeline(继承 Phase 3 §5.2,标注代码基础)

| 步骤 | 操作 | 基础 |
|------|------|------|
| 1. Resolve | 读 manifest `dependencies`,缺失依赖递归安装(深度上限 3,环检测) | 新增 |
| 2. Validate | `compatibility.narranexus_min/max` vs 当前版本(pyproject version) | 新增 |
| 3. Conflict | 同名同版本 → 跳过;同名异版本 → UI 询问 / Prompt 驱动直接替换 + config 同名 key 迁移 | 新增 |
| 4. Download & Verify | 从 S3(marketplace)或 URL 下载,sha256 对比 catalog 记录,不一致拒绝 | 扩展 `install_skill()` |
| 5. Unpack | 复用现有 zip 安全检查(500 entries / 100MB / zip-slip)解到 `skills/<skill_id>/` | 已有 |
| 6. Inject Config | `config_schema` 注册进 Skill 配置面板;必填项缺失 → 状态 `Needs Config` | 新增 |
| 7. Lock & Audit | 更新 `.skill_meta.json`(+`hash`/`content_hash`/`updated_at`)→ upsert `skill_installations` → `backup_after_api_install()` | 扩展已有 |

更新/回滚:继承 Phase 3 §5.4(`skills/.archive/` 备份旧版、一键回滚、默认通知不自动更新)。

### 5.4 Agent 侧 MCP 工具(`_skill_mcp_tools.py` 新增 3 个)

- `skill_search_marketplace(agent_id, user_id, query, capability?)` → 候选列表(id/描述/评分/扫描状态)
- `skill_install(agent_id, user_id, skill_id_or_url, version?)` → 走 InstallPipeline;装完若有未配置必填项,回复中附「去配置」提示(Phase 2 comment #4 的体验)
- `skill_uninstall(agent_id, user_id, skill_id)` → 走 Pipeline 卸载分支

桌面端这三个工具透过 RegistryClient 调 cloud API;离线时返回明确的"marketplace unavailable"错误语。

---

## 六、API 规范(前缀 /api/marketplace/skills)

Marketplace 命名空间按对象拆两个子前缀:**`/api/marketplace/skills/*`**(本项目)与 **`/api/marketplace/teams/*`**(预留给 agent/team bundle 分享,本项目不实现、不占用)。新增路由文件 `backend/routes/marketplace_skills.py`(身份一律走现有 `resolve_current_user_id`;字面路由 `updates`/`publish` 先于 `{skill_id}` 声明):

| Method & Path | 说明 |
|---|---|
| `GET /api/marketplace/skills/search` | 参数 q, category, capability, tags, sort(downloads/published/name), page, limit, agent_id?;传 agent_id 时注入 `installed` / `update_available`(对比该 workspace `.skill_meta.json`) |
| `GET /api/marketplace/skills/updates` | 已装清单(或传 agent_id 服务端自查),返回可更新列表 |
| `GET /api/marketplace/skills/{skill_id}` | 详情 = 最新 manifest + 版本历史 + 最新扫描结果 |
| `POST /api/marketplace/skills/{skill_id}/install` | Body: {agent_id, version?, auto_migrate_config?};200 {status, needs_restart, config_required};409 SKILL_ALREADY_INSTALLED |
| `POST /api/marketplace/skills/publish` | cloud-only、内部权限;multipart zip;流程见 §4.2;422 rejected + scan_report |

统一错误码继承 v1.0:400 INVALID_PARAM / 404 SKILL_NOT_FOUND / 409 CONFLICT / 422 INCOMPATIBLE_VERSION·SECURITY_SCAN_FAILED / 429 RATE_LIMITED / 500 INSTALL_FAILED。

现有 `/api/skills` 的 10 个端点(list/install/remove/disable/enable/study/env/detail)全部不动;URL/GitHub/zip 安装内部改为经过 InstallPipeline(对调用方透明,响应结构不变)。

---

## 七、安全 Pipeline(定位修订)

**主防线 = 框架无关的静态扫描 Gate**(发布时 cloud 扫一次 + 安装时本地复扫一次,双端同一份规则代码);**运行时隔离 = 当前 runtime backend 的实现细节**(Codex 三层:CODEX_HOME 隔离 + TOML permissions + env allowlist;Claude SDK 路径有各自的沙箱语义)。这样换 Agent 框架(铁律 #9)时安全主防线不失效。

扫描规则集、HIGH/LOW 分级、Known Gaps(symlink 逃逸 / pip 无条件拒绝 / glob vs regex / danger-full-access)全部继承 Phase 4 §7,不再重复。补充两点:

1. **安装时复扫**:URL/GitHub 来源没有经过发布 Gate,InstallPipeline 第 4 步下载后、第 5 步解包前跑同一 Scanner;HIGH 拒绝安装,LOW 展示风险项由用户确认(UI)/ 记入回复(Agent 路径)。Marketplace 来源已扫过,本地只校验 hash 不重复扫。
2. Scanner 代码放 `_skill_marketplace_impl/scanner/`(`static.py` 规则引擎、`patterns.py` 规则表、`audit.py` 依赖审计),纯 Python AST + 正则,无外部服务依赖,桌面端可直接运行。

### env_config 加密(4 号决策)

- 算法:`cryptography.fernet`(AES-128-CBC + HMAC,标准库级成熟方案)。
- 密钥:local 模式首次使用时生成 `~/.nexusagent/keys/skill_secrets.key`(目录与文件 0600);cloud 模式读环境变量 `SKILL_SECRETS_KEY`,未设置则启动时 error log + 回退文件方案(单 pod 可用,多 pod 部署必须注入 env)。
- 迁移:读取 `.skill_meta.json.env_config` 时先尝试 Fernet 解密,失败则按旧 base64 解码读出,并立即用 Fernet 重写回文件(惰性一次性迁移,无独立迁移脚本)。
- `bundle/skill_secrets.py::scrub_skill_meta` 行为不变(导出时默认剥值留键)。
- 实现位置:`skill_module.py` 的 `set_skill_env_config` / 读取路径,加解密封装成 `_skill_marketplace_impl/secret_box.py` 供两处共用。

---

## 八、前端

- **Skill Tab(已有 SkillsPanel)**:安装列表增加 Source 列(Marketplace/URL/GitHub/Builtin/Manual)与状态徽标(Active / Needs Config / Update Available / Unmanaged←对应 `modified`/`manual`);右上角「Add Skill」进入 Marketplace 浏览。
- **Marketplace 浏览/详情**:新增 `components/skills/marketplace/` 下 `MarketplaceBrowser.tsx`(搜索框 + category/capability facet + 卡片列表)、`SkillDetailSheet.tsx`(描述/capabilities/config schema 预览/扫描结果/版本历史/Install 按钮)。复用现有 `InstallDialog.tsx` 的安装反馈模式。
- **数据层**:`lib/api.ts` 增加 marketplace 五个调用;`hooks/useSkillMarketplace.ts`(TanStack Query,search 带 300ms debounce);类型进 `types/skills.ts`。
- 独立全局 Marketplace 页(Phase 3 §6.1 入口二)推迟,MVP 只做 Skill Tab 内入口。

---

## 九、文件清单(结构维度)

新增(核心包 9 + 后端 1 + 服务 1 + 前端 4 + 测试 6 ≈ 21 files):

```
src/xyz_agent_context/
├── skill_marketplace_service.py                  # service 协议层
├── _skill_marketplace_impl/
│   ├── registry.py                               # cloud: catalog 查询/发布; RegistryClient(桌面)
│   ├── install_pipeline.py                       # 7 步引擎(封装 SkillModule 现有 API)
│   ├── artifact_store.py                         # S3 封装(boto3 仅此一处)
│   ├── secret_box.py                             # Fernet 加解密
│   └── scanner/{static.py, patterns.py, audit.py}
├── services/skill_sync_service.py                # 对账器 poller
├── repository/{skill_catalog, skill_installation, skill_scan_result}_repository.py
├── schema/skill_marketplace_schema.py
backend/routes/marketplace_skills.py              # /api/marketplace/skills/* 路由
frontend/src/components/skills/marketplace/{MarketplaceBrowser,SkillDetailSheet}.tsx
frontend/src/hooks/useSkillMarketplace.ts
frontend/src/types/(扩展 skills.ts)
tests/marketplace/{test_scanner,test_install_pipeline,test_registry,test_reconciler,test_secret_box,test_api}.py
```

修改(5 files):`skill_module.py`(env 加密接线 + WORKSPACE_RULES 新条目)、`_skill_mcp_tools.py`(+3 工具)、`utils/schema_registry.py`(+4 表)、`backend/routes/skills.py`(现有安装入口接 Pipeline)、`frontend/src/components/skills/SkillsPanel.tsx`(入口 + 状态列)。

铁律 #10:以上每个新源文件同 commit 配 `.mindflow/mirror/` md;修改的 5 个文件同 commit 刷新 mirror md 的 intent 段与 `last_verified`。

---

## 十、测试策略(TDD,继承占比)

- 单元(pytest):Scanner 12 条规则逐条正反例、hash 校验、manifest 解析/合成、conflict 判定、**reconciler 四种对账分支**、secret_box 加解密 + base64 惰性迁移。
- 集成:InstallPipeline 端到端(marketplace/url/github 三源)、API 路由(sqlite 内存库)、对账器 + 真实文件系统 fixture。
- 前端(vitest):MarketplaceBrowser 搜索/安装状态流转,沿用 `SkillsPanel.studyStatus.test.tsx` 模式。
- 安全:恶意 skill 样本集(shell pipe / 敏感路径 / zip-slip / oversize)全部被 Gate 拦截。
- 铁律 #7 验收:桌面 DMG 与 `bash run.sh` 各跑一次 happy path(marketplace 搜索→安装→重启生效→卸载)。

---

## 十一、实施结构评估(铁律 #17:不用人天)

| 维度 | 评估 |
|------|------|
| 触及层数 | 5(schema / repository / service+impl / route / UI),Agent Runtime 零触及 |
| 新增文件 | ~21;修改 5;新表 4(均为加法,无 destructive migration) |
| 前置任务 | S3 bucket + IAM(dev server 已有,见 PRD comment);`SKILL_SECRETS_KEY` 注入 cloud 部署 |
| 分段交付顺序 | ① 表 + repository + secret_box(独立可测)→ ② Scanner(独立可测)→ ③ InstallPipeline 重构接线(现有安装路径回归)→ ④ Registry + Publish + S3 → ⑤ API + MCP 工具 → ⑥ 对账器 → ⑦ 前端 → ⑧ 5 个 MVP skill 发布 + 双模式 e2e |
| 风险等级 | ③ 是唯一动现有行为的段(现有安装入口改走 Pipeline),独立可回滚;其余全部为纯新增 |
| 测试覆盖 | 单元 + 集成 + 前端组件 + 安全样本集 + 双模式手工验收 |

---

## 十一-B、实施进度跟踪(每段完成后更新本节)

> 约定:每完成一段,把该段状态改为 ✅、填入 commit 与实际交付记录;开工中标 🔄。
> 本节是唯一进度真相,与 git log 互为印证。

### ① 数据层 + SecretBox — ✅ 已完成(2026-07-20,commit `c4223932`)

**范围**:4 张表注册 + 3 个 Repository + Pydantic schema + Fernet 加密件。

**实际交付**:
- `utils/schema_registry.py`:+`skill_catalog` / `skill_installations`(唯一键 agent_id+user_id+skill_id 三元组)/ `skill_scan_results`(append-only)/ `team_skill_policies`(占位)
- `schema/skill_marketplace_schema.py`:`SkillCatalogEntry` / `SkillInstallationRecord` / `SkillScanResult`(时间字段 `Optional[datetime]`,对齐 SkillArchive 惯例)
- `repository/skill_catalog_repository.py`:publish 幂等 upsert(不重置 downloads)、semver 感知 get_latest、search 每 skill 一张卡 + capability/tag 紧凑 JSON LIKE 匹配、increment_downloads 原子自增
- `repository/skill_installation_repository.py`:upsert_event / mark_status(行永不删,审计留痕)/ list_for_workspace
- `repository/skill_scan_result_repository.py`:record + latest_for(ORDER BY id DESC)
- `_skill_marketplace_impl/secret_box.py`:SKILL_SECRETS_KEY 环境变量(非法值 fail-fast)→ 0600 密钥文件;legacy base64 兼容解密 + `needs_rewrite` 惰性迁移标记;不可解析值原样返回
- `pyproject.toml`:+`cryptography>=42.0.0`

**验收**:22 个新测试全绿(TDD);tests/bundle 45 个回归全绿;ruff 干净;pyright 新文件 0 错误;全部新文件配 mirror md。

### ② Security Scanner — ✅ 已完成(2026-07-20)

**范围**:`_skill_marketplace_impl/scanner/`(`static.py` 规则引擎、`patterns.py` HIGH/LOW 规则表、`audit.py` 依赖审计)。框架无关主防线,双端共用;HIGH→REJECT,LOW→WARN。

**实际交付**:
- `scanner/patterns.py`:2 条 HIGH 文本规则(shell_pipe_exec / sensitive_path)+ 10 条 LOW AST 规则 + 2 条额外 LOW(`unparsable_python`——语法错误不能成为绕过 AST 的通道;`vulnerable_dependency`)。`SCANNER_VERSION` 常量随规则集升级。
- `scanner/static.py`:目录遍历 + 文本正则(先跑)+ Python AST 调用点匹配;二进制/超 1MB 文件跳过;`.skill_meta.json` 排除;聚合 rejected/warning/passed。
- `scanner/audit.py`:requirements.txt 精确 pin 对照 in-repo advisory 表(静态 MVP,升级 Safety/OSV 只动这一处);离线可用。
- 规则修正:`credentials` 收紧为路径形态匹配(`.aws/credentials` 等),裸英文单词出现在 skill 文档中不再误杀——带回归测试。

**验收**:31 个 scanner 测试全绿(每条规则正反例 + 语法错误绕过 + 二进制跳过 + 误报回归);全套 53 个 marketplace 测试绿;ruff/pyright 干净;4 个源文件 + 测试全部配 mirror md。

### ③ InstallPipeline 接线 — ✅ 已完成(2026-07-20)

**范围**:`_skill_marketplace_impl/install_pipeline.py` 7 步引擎;现有 zip/GitHub 安装入口改为经 Pipeline(响应结构不变);`.skill_meta.json` 增加 `hash`/`content_hash`/`updated_at`;env_config 读写接 SecretBox。

**实际交付**:
- `install_pipeline.py`:7 步引擎。扫描 Gate 在 staging 阶段跑(HIGH 拒绝时 `skills/` 完全不被触碰、旧版本安然无恙);同版本冲突 → `already_installed` 不动盘;替换时先捕获旧 `env_config` 装完合并回(同名 key 自动迁移);审计/归档失败只记 log 绝不回滚磁盘(disk is truth)。
- `skill_module.py` 重构:安装流程拆为三个公开原语 `extract_skill_package` / `fetch_github_repo` / `install_from_dir`,老 API(`install_skill`/`install_from_github`)变薄壳、行为不变(bundle 导入路径不受影响);另加 `merge_skill_meta`/`read_skill_meta`/`parse_skill_package`,pipeline 零私有访问。
- **env_config 加密上线**:`set_skill_env_config`/`get_all_skill_env_vars` 接 SecretBox(Fernet),旧 base64 值读到即惰性迁移重写;对前端/MCP 完全透明(env 值从不出后端,接口只回 presence bool)。
- `backend/routes/skills.py`:install/remove 端点接 Pipeline,`backup_after_api_install` 移入 pipeline。行为差异仅:恶意包 400 拒绝;消息可能带 "(N security warnings)";同版本重装返回 already-installed(原为静默覆盖)。

**验收**:11 个 pipeline 测试 + **全量套件 2812 passed / 0 fail 零回归**;ruff 干净;pyright 新文件 0 错误(所touch旧文件存在 9 个改动前已有的类型噪音,未引入新错误);mirror md 同步(skill_module.py.md / skills.py.md 加dated entry)。

### ④ Registry + S3 — ✅ 已完成(2026-07-21)

**实际交付**:
- `artifact_store.py`:S3(`SKILL_S3_BUCKET`/`SKILL_S3_PREFIX`/`SKILL_S3_REGION` env)+ LocalArtifactStore 双实现,boto3 仅此一处、懒加载;key 逃逸防护。**注意:cloud 部署要配 `SKILL_S3_BUCKET` + IAM;未配则回退本机 marketplace_store 目录(单机可用)。**
- `registry.py`:Publish 流水线(extract 复用 zip 安全检查 → manifest 校验/合成、version 必填 → 扫描 Gate(`PublishRejectedError` 带完整报告)→ artifact+manifest 上传 → catalog+scan 行);search/detail/check_updates/download_to;`LocalMarketplaceSource`(cloud)与 `RemoteMarketplaceSource`(桌面 → cloud API,信任 `X-Skill-Version`/`X-Package-Hash` 响应头)。
- InstallPipeline 新增 `install_from_marketplace`:下载 → **hash 实时校验防篡改** → 依赖递归安装(深度≤3+环检测)→ skip_scan 安装(发布时已扫)→ 下载计数。
- 删除 S3 index 文件的决策落地:DB catalog 是唯一目录真相。

### ⑤ API + MCP 工具 — ✅ 已完成(2026-07-21)

**实际交付**:
- `skill_marketplace_service.py`:协议层唯一门面;**cloud/desktop 分流决策只在这一处**(cloud→本地 DB registry,desktop→cloud API);installed/update_available 注入按磁盘真相(SkillModule + meta)。
- `backend/routes/marketplace_skills.py` 挂 `/api/marketplace/skills/*`(teams/* 预留):search / updates(agent 模式+批量 skills= 模式)/ {id} detail / {id}/download(带版本与 hash 头,服务端计数)/ {id}/install(409 SKILL_ALREADY_INSTALLED)/ publish(`MARKETPLACE_PUBLISH_TOKEN` 门禁,422 带 scan_report)。读端点公开(包注册表语义),写端点走 auth。
- `_skill_mcp_tools.py` +3 工具:`skill_search_marketplace` / `skill_install`(marketplace id 或 GitHub URL 双路)/ `skill_uninstall`;离线返回明确不可用消息,不抛异常打断 agent loop。
- `skill_module.py` 新增 `SKILL_MANAGEMENT_RULES` 同文追加进 CLOUD/LOCAL 双 prompt(铁律 #7):禁手工动 `skills/`,必须走三个工具。

### ⑥ 对账器 — ✅ 已完成(2026-07-21)

**实际交付**:
- `services/skill_sync_service.py`:五种漂移分支(manual 补录/external_removed/modified(content_hash)/disabled/restored),幂等,**只写 DB 永不动用户文件**(有专门的不变量测试);`reconcile_all` 按 nested `{user}/{agent}` 布局遍历。
- 接线:backend lifespan 启动先跑一遍 + `run_forever` 循环(`SKILL_SYNC_INTERVAL_SECONDS` 默认 1800,0 关闭),shutdown cancel + done-callback 记录异常退出。两种运行模式共用同一 lifespan(铁律 #7)。
- 与计划的差异:未做"每次安装后增量对账"——InstallPipeline 自己写审计行,增量对账冗余;enable/disable 的 UI 路径靠周期对账收敛(DB 仅审计用途,滞后 ≤ interval 可接受)。

**④⑤⑥ 合并验收**:tests/marketplace 84 个全绿(registry 8 + api 7 + reconciler 7 + 既有 62);全量套件 2832 passed 零回归;ruff 干净;pyright 新文件 0 错误。

### ⑦ 前端 — ✅ 已完成(2026-07-21)

**实际交付**:
- `components/skills/marketplace/MarketplaceBrowser.tsx`(搜索 300ms debounce、卡片带扫描徽标/下载量/installed/update 标记、安装按钮、离线 unavailable 态)+ `SkillDetailSheet.tsx`(描述/capabilities/config keys 预览/扫描结果/版本历史/安装)。
- `hooks/useSkillMarketplace.ts`(search/detail/install/updates,install 同时失效 skills 与 marketplace 两个 query key)。
- `lib/api.ts` 四调用;`types/skills.ts` 新增 marketplace 类型 + `SkillInfo.source_type`(后端 `_parse_skill_md` 回填,配套 `skill_schema.py` 加字段)。
- `SkillsPanel.tsx` action bar 加「Marketplace」按钮;`SkillCard.tsx` 显示 Source 徽标。
- i18n:en + zh 全套 key(其余 8 语言走 fallbackLng=en,后续补翻译)。
- 与计划的差异:**Unmanaged 徽标推迟**——它需要列表接口暴露审计状态(fs 列表实时算 content_hash 太贵),留给后续把 `/api/skills` 响应拼上审计状态时一并做。

**验收**:`tsc -b` 干净;新改文件 eslint 0 错误(仓库存量 77 个 error 与本次无关);vitest 全量 555 passed(含 5 个新 MarketplaceBrowser 测试)。

### ⑧ MVP Skills + 双模式验收 — 🔶 部分完成(2026-07-21,剩余项见下)

**已交付**:
- `scripts/publish_skill.py` 发布 CLI(zip 或目录一键发布,422 时打印完整扫描报告,可进 CI;exit code 区分 rejected/error)。

**2026-07-21 试跑结果(本地全链路已打通)**:
- **首批 5 技能定稿**(选自同事的 Source & Installation Summary,均 clawhub 拉取 + 补 manifest.json,扫描全 passed):`ddg-search`(免费网页搜索,零依赖)/ `markdown-converter`(PDF/DOCX→MD)/ `gh`(GitHub CLI,⭐)/ `api-tester`(HTTP API 测试)/ `chain-of-density`(纯 prompt 摘要方法论,零依赖)。打包件持久化在 `~/Desktop/xyz_proto_test/NarraNexus_mvp_skill_packages/`。
- **ggshield-scanner(⭐)被我们的 Gate 拒绝**(11 HIGH:文档合法讨论 `.env`/凭证路径 + README 有真 `curl|bash` 安装指引)→ 换成 api-tester。规则课题记录:安全类技能"讨论敏感路径"vs"访问敏感路径"如何分级,待定(可选方向:仅对 .md 文档降级为 WARN、代码文件维持 REJECT)。
- **端到端验证**:隔离 registry 实例(cloud 模式、临时 sqlite、本地 store)→ CLI 发布 5 个 → 匿名搜索可见 → 桌面路径(RemoteMarketplaceSource,hash 校验)安装进测试 workspace → 审计行/来源/版本正确 → 服务端下载计数 +1。
- **发现并修复真 bug**:cloud 全局 auth 中间件拦掉 marketplace 读端点(桌面端无 cloud JWT 拉不到目录)→ GET 读面改「可选认证」+ publish 走自带 token 豁免(见 auth.py mirror 2026-07-21 条目)。

**剩余项**:
1. **S3 bucket + IAM**——已核实 dev server 上没有任何 S3 配置(无 `~/.aws`、stack .env 无 AWS key、实例无 IAM role),需要在 AWS 账户新建 bucket + 发 key,然后给 cloud 部署注入 `SKILL_S3_BUCKET`(未配则自动回退本机 `~/.nexusagent/marketplace_store/`,单机模式已验证可用);同时注入 `MARKETPLACE_PUBLISH_TOKEN` 与 `SKILL_SECRETS_KEY`;
2. **双模式手工验收**(铁律 #7)——DMG 与 `bash run.sh` 各跑:Marketplace 搜索→安装→重启生效→配置→卸载 + Agent 侧 `skill_install` 一次;
3. 上架来源合规:5 个技能来自 clawhub 社区(Apache-2.0 等),正式对外上架前确认 license/署名展示(manifest 已带 provenance_note)。

### ⑨ Default Skills(NetMind 多模态补齐)— ✅ MVP 已完成(2026-07-21)

**背景**:DeepSeek V4 等模型缺多模态,用 NetMind API 撑腰的技能补齐;用户已配 NetMind 则零配置可用(Hongyi 需求)。

**实际交付**:
- **平台可解析 env(密钥复用 + 隔离)**:`PLATFORM_RESOLVED_ENV=("NETMIND_API_KEY",)`——技能声明该变量且用户未显式配置时,运行时从该 user 的 `user_providers`(source=netmind)取 key **只注入子进程环境、永不落盘**;显式配置优先;key 轮换即时生效;cloud 无额外密钥副本。UI 侧平台变量不再触发 Needs Config。
- **两个第一方技能**(`marketplace_skills/` 源目录,纯 stdlib、扫描 0 issue):
  - `netmind-vision`:图片理解,OpenAI 兼容端点 + `Qwen/Qwen3-VL-235B-A22B-Instruct`。**已实测通过**(读出测试图文字)。
  - `netmind-transcribe`:音频转文字(`openai/whisper`)。URL → 原生 submit/poll 流(可用,需公网可取 URL);本地文件 → 兼容端点 multipart——**NetMind 侧当前 500**(真实语音亦复现,request_id `f001d463…`/`f350908c…` 已交 Hongyi 内部报障),脚本失败时明确提示走 URL 路。
- **default 装机机制**:`skill_catalog.is_default` 列(additive)+ manifest `"default": true` → `list_defaults()` + 公开 `GET /defaults` 端点 + `service.install_defaults()`(单技能失败只记录、registry 不可达优雅跳过)→ `create_agent` 成功后 fire-and-forget 自动装(带 done-callback,不阻塞不致命)。
- 两技能已以 default 标记发布进本地 registry(目录现有 7 技能)。

**验收**:8 个新测试(default 标记/最新版/装机/降级/key 注入/显式优先/无 provider 不注入/不落盘/UI 视为已配置);全量 2843 passed 零回归;ruff/pyright 干净。

**遗留**:officecli/home-assistant 仍走 builtin 物化(离线兜底),统一进 registry 是下一步;模型能力清单(按模型缺口选装)代码库尚无能力数据,PRD §2 的按模型选装推后;NetMind 文件转写端点修复后 `netmind-transcribe` 本地文件路自动痊愈(无需改动)。

---

## 十二、风险清单(修订后)

1. **第三方代码执行(不变,最高)** — 缓解:MVP 不开社区上传 + 双端扫描 Gate + 运行时隔离 + Known Gaps 文档化。
2. **Codex 权限覆盖缺口(不变)** — 缓解:Gate 补偿 + Codex issue #16685 跟踪 + 框架无关主防线定位(§7)。
3. ~~Registry Index 一致性~~ — **已通过删除 S3 index 文件消除**;残余风险仅为下载瞬间 catalog 变更,靠安装前 hash 实时校验兜底。
4. **磁盘/DB 漂移** — 新增,缓解即 §5 三道防线;残余:对账周期内(≤30min)DB 短暂滞后,可接受(DB 仅审计用途)。
5. **版本兼容断裂(不变)** — 缓解:compatibility 字段强校验;Top skill 回归矩阵推迟到技能数上来之后。
6. **Default Skill 过度安装(不变,属 Default Skill 子项目)** — 白名单制 + 可卸载。

---

## 十三、明确不做 / 推迟

- 社区上传、评分评论、Team Recommended(表已占位)、独立 Marketplace 全局页、裸 SKILL.md URL 安装(P2)、Agent-callable Recommend API(先做 Search/Install 两个 MCP 工具)、ES/向量搜索、Redis 缓存、多 pod 对象存储化 `skill_archives`(已有 scaling_assumptions.md 记录,与本项目解耦)。
- Team/Agent Marketplace(`feat/in-app-marketplace` 分支)保持互不依赖;命名空间约定:它未来落位 `/api/marketplace/teams/*`(其现有 `/api/marketplace/templates` 路由在合并时迁移),本项目只占用 `/api/marketplace/skills/*`。
