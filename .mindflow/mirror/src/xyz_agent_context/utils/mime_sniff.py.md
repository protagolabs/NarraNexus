---
code_file: src/xyz_agent_context/utils/mime_sniff.py
last_verified: 2026-08-12
stub: false
---

# mime_sniff.py — the single tiered MIME sniffer

## 2026-08-12 — libmagic 从「可选」变硬依赖（Mark item 8）

代码不变，但 tier 1 的 libmagic 此前只是名义上的「optional dependency」：`python-magic` 从未在 deps 声明，`ImportError` 分支被静默吞，实际永远退化到可伪造的扩展名 / 客户端 Content-Type（WAV 改名 .png + 声明 image/png 被当图片）。本次把 `python-magic>=0.4.27` 加进 `pyproject.toml` deps，并在 `docker/Dockerfile.manyfold` apt 装 `libmagic1`（native lib）——生产环境 tier 1 现在真正生效，内容判定优先于伪造的名字/类型。`ImportError` 兜底保留（本地未装 libmagic 仍能降级不崩）。

**tier-1「无信息」判据扩展**：libmagic 装上后，空字节 `magic.from_buffer(b"")` 返回 `application/x-empty`（非 octet-stream）。若只挡 octet-stream，空文件会被判成 x-empty、绕过扩展名兜底，且打挂原有 `test_attachments_sniff` 里用空字节走 tier 2/3 的用例。故新增 `_MAGIC_NO_INFO = {application/octet-stream, application/x-empty, inode/x-empty}`：三者都当「没看出内容」→ 落到扩展名/客户端类型 tier（空的 `.webm` 占位仍能靠扩展名判型）。

## Why it exists

Three entry points receive file bytes plus untrusted naming metadata (browser
chat uploads via [[agents_attachments]], IM-channel downloads via
[[channel_trigger_base]], team-chat uploads via [[teams]]) and each used to
carry its own sniffing copy with subtly different tiering. The divergence was
user-visible: the team-upload copy returned libmagic's
``application/octet-stream`` verdict directly, so a ``.md``/``.csv`` upload
classified as octet-stream on the team path but got its real MIME on the IM
path — and ``mime_type`` drives both the frontend category (thumbnail vs grey
chip) and whether Whisper runs (``audio/*``). PR #141 review consolidated all
three onto this one helper.

## Tiering (first hit wins)

libmagic (an ``octet-stream`` verdict means "no idea" → fall through) →
extension guess → client/platform-supplied type → ``octet-stream``. The
client type is deliberately LAST resort — it's user-controlled — but it also
serves as the audio/video container tiebreaker
(``_audio_video_container_override``, hoisted verbatim from
agents_attachments): WebM/Ogg/MP4 headers look identical for audio-only and
audio+video, so a ``video/<container>`` verdict flips to ``audio/`` when the
client tagged the SAME container as audio. That override is what keeps
in-browser voice memos transcribable.

## Gotcha

For the IM-channel caller the platform ``hint`` used to outrank the extension
guess; under the unified tiering it now plays the client-type role (tiebreaker
+ last resort). The two only disagree when a platform supplies a MIME that
contradicts the file's own extension — and content sniffing still outranks
both.
