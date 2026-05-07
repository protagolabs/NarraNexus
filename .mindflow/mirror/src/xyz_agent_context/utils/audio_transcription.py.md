---
code_file: src/xyz_agent_context/utils/audio_transcription.py
last_verified: 2026-05-04
stub: false
---

# audio_transcription.py

## Why it exists

Single-purpose Whisper-API client used only by the chat-attachment upload
route. It exists as its own module (rather than living inline in the
route) because:

- **Provider routing is non-trivial** — 4-tier fallback through user
  provider → system default → settings — and it must be tested in
  isolation from FastAPI request handling.
- **The "never raises" contract** must be obvious to readers. Putting it
  in its own module with a single public function (`transcribe_audio`)
  makes the contract impossible to miss; it's documented in the module
  header and enforced by a top-level `try/except Exception`.
- **Future migration off OpenAI Whisper** (e.g. on-device, Gemini
  audio-input chat models) lands in this file alone.

The module never owns persistent state. It's a stateless utility called
synchronously during upload — the caller awaits, gets back `str | None`,
done.

## Upstream / Downstream

Upstream:
- `backend/routes/agents_attachments.py::upload_attachment` calls
  `is_transcription_available` (cheap pre-check) then `transcribe_audio`
  on `audio/*` MIME uploads.

Downstream:
- `xyz_agent_context.agent_framework.user_provider_service.UserProviderService.get_user_config`
  — Tier 2 of the credential chain. Lazy-imported to avoid a top-level
  `utils → agent_framework` cycle.
- `xyz_agent_context.agent_framework.system_provider_service.SystemProviderService`
  — Tier 3 of the credential chain. Same lazy-import treatment.
- `xyz_agent_context.settings.settings` — Tier 4 (`openai_api_key`).
  No dedicated `whisper_*` overrides exist; users wanting self-hosted
  whisper.cpp configure it as a regular OpenAI-protocol user_provider
  and the chain picks it up at Tier 2.
- `httpx.AsyncClient` — direct HTTP to the resolved provider's
  `/audio/transcriptions` endpoint.

## Design decisions

**Reuse the chat provider system, don't ship a parallel Whisper config.**
NarraNexus already has `user_providers` storing per-user OpenAI-protocol
credentials for chat / embedding / helper_llm slots. Whisper rides on the
same infrastructure: any user with a **multipart-compatible** OpenAI
provider gets transcription without configuring anything new. This
mirrors the architectural rule that each LLM capability rides on top of
provider routing rather than hardcoding a vendor.

**Provider acceptance is narrower than the chat path.** Chat accepts
any OpenAI-protocol provider (NetMind / OpenRouter included), but
`_is_compatible_provider` rejects NetMind AND OpenRouter for
transcription:

- **NetMind**: Whisper at `/v1/generation` + `audio_url` (needs public
  URL hosting we don't have).
- **OpenRouter**: Whisper at `/audio/transcriptions` but JSON+base64,
  not multipart. Skipped at the resolution gate so users with only an
  OpenRouter key get a clean "transcription unavailable" hint instead
  of silent 4xx failures from the multipart dispatcher. The model-id
  normalization (`openai/whisper-1`) and dispatcher-level URL
  composition stay in place so when the JSON+base64 branch lands, the
  rest of the wiring is already correct.

Currently supported in production: OpenAI official + Yunwu (and any
self-hosted whisper.cpp behind an OpenAI-shaped multipart endpoint).

**Never raise. Period.** Upload is a synchronous request path —
transcription is best-effort enrichment, not a hard dependency. The
upload must always succeed (or fail for storage / size / sandbox
reasons, never for transcription). The module enforces this with a
final `except Exception` and `logger.error` rather than re-raising.

**Three-layer timeout, no outer wait_for in the route.** Mirrors
`web_search_brave_tool.py`: `httpx.Timeout` per call,
`asyncio.wait_for` around `_call_whisper`, and the route does NOT add
its own timeout because the module's `_OVERALL_TIMEOUT_S = 35s` is
already tight enough for a synchronous upload handler.

**Defensive 25 MB guard inside the module, not just at upload.**
`backend.config.max_upload_bytes` defaults to 50 MB — bigger than
Whisper's hard 25 MB limit. Catching oversize uploads here means we
don't waste a Whisper call (would 4xx) and we get a clear log line
about WHY the transcript is null.

**OpenRouter model-id prefix is wired but not yet reachable.**
OpenRouter requires `openai/whisper-1` rather than `whisper-1`.
`_normalize_model` detects by `base_url` containing `openrouter.ai`
and adjusts. As of this commit OpenRouter is rejected upstream by
`_is_compatible_provider`, so the prefix logic only runs in tests
today — kept in place so the dispatcher branch (JSON+base64) only has
to add the body-shape change without touching URL/model wiring.

## Gotchas

- **Re-open the file on every retry.** The httpx file handle is
  exhausted after a send; reusing the same `fp` on retry posts 0
  bytes. Tests cover this implicitly (the retry test would 401 if the
  body were empty), and the production code uses
  `with path.open("rb") as fp:` inside the retry loop.
- **Lazy imports for `UserProviderService` and `SystemProviderService`.**
  `xyz_agent_context.utils` is imported very early in app startup; the
  agent_framework modules depend on it. A top-level import here would
  produce a circular reference. The code handles this with
  function-local imports inside `_resolve_credential`.
- **Empty response treated as None.** Whisper sometimes returns `""`
  for silent / noise-only audio. The marker-synthesis path treats
  `transcript=""` as "no transcript" anyway (truthy check), but the
  module returns `None` explicitly so the upload response is clean.

## New-joiner traps

- Don't add a "real Whisper integration test" that hits the live API.
  Tests must mock `httpx.AsyncClient` (see
  `tests/utils/test_audio_transcription.py`) — anything else makes CI
  flaky and burns credits.
- Don't introduce a separate `WHISPER_*` env-var path that bypasses
  the user provider system. The point of Tier 2 is that users who
  already configured chat get transcription "for free". A new env
  override invites drift between chat and transcription credentials
  for the same user.
- The function-level retry / sleep is on `at.asyncio.sleep` for
  monkeypatchability. Don't inline `time.sleep` — it would block the
  event loop and break the upload latency budget.
