# Changelog

## Unreleased

- Voice contract v15, step 2: the trigger now receives Narra's control events
  (`call.started`, `turn_hint`, `turn.cancelled`, `call.ended`), answers them
  with `ai.netmind.rtc.agent.ack.v2`, claims each `(rtc_session_id, turn_id)`
  once before running it, and does not start a turn that was cancelled before
  it began (whether its input arrives late or it is queued behind a running
  turn). Nothing is acked for agents still on guide v14 (they receive no
  control events). Interrupting a turn that is already running is not part of
  this step.
- A voice turn's fallback delivery now edits the live reply already in the
  room instead of sending a second message.
- Voice contract v15, step 1: every event that answers a voice turn (live
  reply and its edits, plain fallback, `narra_reply` fresh-send, error marker,
  circuit refusal, platform no-reply fallback) now carries the
  `ai.netmind.rtc.voice_reply` binding. Text turns are unchanged.
- `speak` is now registered as a reply tool for the runtime's "was this turn
  answered" check. A voice turn answered only through `speak` no longer
  triggers the DM no-reply fallback (a second, helper-written message).
- The narra_cli failure instructions on all three agent-facing surfaces
  (`_CLI_CAPABILITY`, `resources/narra-runtime.md`, `_narra_guide._BUILTIN`) now
  pass `dedup_key="narra_cli:<code>"` to `submit_feedback` — that key is what
  makes the report one per agent per code, and a caller omitting it gets no
  deduplication at all. They also relay whether the team was notified from the
  call's result instead of asserting it.

## 1.0.0

- Packaged as an independent workspace member (plugin platform batch 6b).
