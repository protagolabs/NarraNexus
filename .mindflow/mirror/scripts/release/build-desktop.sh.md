---
code_file: scripts/release/build-desktop.sh
last_verified: 2026-09-10
stub: false
---

# scripts/release/build-desktop.sh — macOS DMG 构建

## 为什么存在

把仓库变成一个可分发的 .app：独立 Python 解释器 + 依赖、Node 运行时 + 两个 CLI、OfficeCLI、Lark skill 包、前端 dist、Tauri 壳，然后签名 / 公证 / 打 DMG。由 `.github/workflows/build-desktop.yml` 在 tag 上跑。

## 依赖安装必须走 uv.lock（2026-09-10 事故）

step 3 曾经是 `uv pip install "$PROJECT_ROOT"`——它按 pyproject 的**区间**重新解析，完全绕过 uv.lock，于是 DMG 装的是"构建当天 PyPI 长什么样"。那天 PyPI 上是 mcp 2.2.0（lock 写的是 1.24.0），`streamablehttp_client` 已改名，`mcp.client.websocket` 已删除，`utils/mcp_executor.py` 两个都 import，结果是发出去的包里每个服务都起不来。当天两种解析对比：176 个包里 99 个与 lock 不同（starlette 0.50→1.6、anthropic 0.72→1.4、fastmcp 2→4）——这个 DMG 跑的是一套从没有人测过的依赖图。

云侧从来没中招：`docker/Dockerfile.manyfold` 走 `uv sync --frozen`。这里是全仓唯一**按 pyproject 区间重新解析、且把结果交到用户手上**的安装路径。（`run.sh` / `deploy-cloud.sh` 的 `uv sync` 读 lock，只是可能顺手刷新它；`run.sh` 里 venv 重建的 fallback 分支那句 `uv pip install -e` 漏了 `--no-deps`，确实会重新解析，但只影响本地 dev venv，且紧跟一句 import 校验。）

现在的形状是 `uv export --locked` → `uv pip install -r`。**必须是 `--locked` 而不是 `--frozen`**：uv 里 `--frozen` 是"导出前不要更新 lock"，陈旧的 lock 会被**静默使用**；`--locked` 才是"断言 lock 不会变，会变就退非零"。所以有人加了依赖忘了 `uv lock`，构建会红在这一步，而不是打出一个缺包的 bundle——同一条用户可见的 ImportError，换了个触发条件。（`verify_release_artifacts.sh` 反过来故意用 `--frozen`：它只想解析 lock，不能因为本地 uv 与 CI 的 lock 格式差异而失败。）守门测试 `tests/release/test_desktop_build_uses_lock.py`。

## 其它已固化的决定（都各自付过学费）

- **非 editable**：editable 会把构建机的绝对源码路径写进 site-packages，装到别人机器上全线 ModuleNotFoundError。
- **不带 `--extra plugins`**：桌面版是轻量版，claude-agent-sdk（~186 MB）由 Settings → Plugins 按需装；分类见 `tests/backend/test_plugins_extra_lockstep.py`。
- **CI 里的 uv 也钉版本**（`.github/workflows/build-desktop.yml` / `ci.yml` 的 `setup-uv` 带 `version:`）。`--locked` 断言的是"这个 uv 导出的 lock 一个字节都不变"，所以 uv 版本本身进了构建契约：不钉的话，哪天 setup-uv 装到一个会抬 lock 格式的新 uv，这一步会在**打完 tag 之后**红，是最贵的失败位置。
- **Python / Node 下载都校验 SHA-256**，且期望值写死在脚本里——不许把"下完再算"的哈希贴回来。
- **bundle 的 npm 依赖钉死在 `scripts/desktop-bundle/package-lock.json`**，用 `npm ci` 装。
- **step 3.1 导入冒烟**：见 `bundle_import_smoke.py.md`。

## 已知仍在漂移的面

- `npx skills add larksuite/cli`（Lark skill 包）没有版本钉子，拿的是构建当天的最新；失败有 WARN + 运行时 fallback 兜底，暂按可接受处理。
- `tauri/src-tauri/Cargo.lock` 在 `.gitignore` 里，Rust 依赖每次构建重新解析。
- `docker/Dockerfile.manyfold` 末尾那句 `uv pip install -e .` 没带 `--no-deps`，理论上也按区间解析；它紧跟 `uv sync --frozen`，lock 的图已就位且满足所有下界，所以实际不会动任何版本。记在这里是为了让"云侧不受影响"这个结论下次审计时不用重新怀疑一遍。
