---
code_file: plugins/builtin.browser/src/narranexus_plugins/browser_module/_browser_module_impl/evidence.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30（复审 2）— 截图证据经受限 fd 写入

`mkdtemp(dir=workspace)` + `write_bytes` + `unlink` + `rmdir` 全按路径，workspace 被换成链接就会把截图写进别的租户的树。现在：随机目录名 + `make_dirs`，
图片用 `open_new`（`O_EXCL|O_NOFOLLOW`，权限 0600），注册失败时 `remove_tree` 清理；workspace 必须在 `settings.base_working_path` 之下，被换成链接 → `ValueError`（调用方已按"证据未保存"处理）。
`resolve_agent_workspace_cwd` 的调用方（lark_cli_client / narra_cli_client 的 subprocess cwd）见 [[utils/confined_io]] 的例外清单。

# Screenshot evidence

Successful policy-gated captures become ordinary image artifacts. The helper
resolves the agent's existing workspace, validates screenshot encoding/type
and artifact size limits, writes a unique image directory and calls
ArtifactService.register with the trusted user and event attribution.

It creates no screenshot table, alternate store or frontend renderer. Images
remain private agent artifacts; no team is inferred from page contents or
model arguments. A registration failure removes the newly written image so
sensitive unregistered evidence is not left behind. Tool responses carry the
artifact reference rather than base64 image data.
