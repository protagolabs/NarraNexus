---
code_file: src/xyz_agent_context/bundle/skill_secrets.py
last_verified: 2026-07-13
stub: false
---

# skill_secrets.py — skill-secret scrubbing for bundle export

## 为什么存在

skill 的 auth 在磁盘上有两种形态:`.skill_meta.json` 里 `env_config` 的环境变量密钥(base64),以及 skill 目录内的凭据文件(`credentials.json` / `*_token*`,后者敏感扫描已识别)。除非导出方勾选 `include_skill_secrets`("full 模式"),否则 skill 密钥不该静默外泄。这个模块是**知道"怎么剥 skill 密钥"的唯一地方**,给 workspace 打包(`_pack_workspace_sync`)和 full_copy 打包(`_zip_dir`)两处复用,避免各写各的、漂移。

## 上下游关系

- **被谁用**:`bundle/builder.py` 的 `_pack_workspace_sync`(workspace 快照里的 `.skill_meta.json`)和 `_zip_dir`(full_copy 归档)。
- **依赖谁**:无(纯函数)。敏感文件名黑名单在 `bundle/security.py`,由调用方各自套用;本模块只管 `.skill_meta.json` 的 env_config 值。

## 设计决策

### 保留键、清值

`scrub_skill_meta` 把 `env_config` 的**值**清空(`{k: ""}`),**保留键 + requires + study_result**。这样导入端仍能看到"这个 skill 需要哪些 env 变量"(便于重配),只是拿不到密钥值。返回 `None` = 没东西可剥(不可解析 / env_config 全空),调用方保留原字节。

## Gotcha

- 只处理 `.skill_meta.json` 的 env_config;skill 目录内的**文件型**密钥(credentials.json 等)靠敏感文件名过滤(`is_sensitive_path`)在调用方各自 drop,不在本模块。
- 未来若做 channel + skill 统一的 `portable_secrets` 抽象(父需求点名的 OO 封装),本模块的 collect/scrub 会并进去;当前保持单一职责。
