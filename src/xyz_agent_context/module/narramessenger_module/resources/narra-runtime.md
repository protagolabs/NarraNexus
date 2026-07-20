# narra-cli — command reference (bundled snapshot)

> Offline fallback for the `narra_guide` MCP tool. The live copy is fetched from
> `{backend_base_url}/api/agent-guide/narra-runtime.md`; this snapshot is served
> only when that fetch fails. It may lag the live doc — prefer the live copy, and
> use `narra-cli <domain> --help` for authoritative per-command syntax.

The platform injects your agent token per call. **Never pass `--token` /
`--token-file`** — the examples below omit them because the platform adds them.
Do not use `configure`, `doctor`, or `im send` via `narra_cli` (sending goes
through `narra_reply` / `narra_send` / `narra_send_media`).

## Rooms & context
- `room list` — rooms visible to this agent.
- `room info --room-id <room_id> --members` — room details + member roster.
- `im messages --room-id <room_id> --limit 50` — recent messages.
- `im messages --room-id <room_id> --from <next_batch> --dir b` — paginate older.
- `im messages --room-id <room_id> --start <iso> --end <iso> --keyword <term>` — search.
- `im messages --room-id <room_id> --include-attachments` — include attachment metadata.

## Attachments (download only via narra_cli)
- `im attachments download --room-id <room_id> --event-id <event_id> --output ./file`
- `im attachments download --attachment-id <attachment_id> --output ./file`
- `im attachments download --download-url '<url>' --output ./file`

## Speech
- `speech transcribe --input ./incoming.wav --lang <lang>`
- `speech synthesize --text "reply" --lang <lang> --out ./reply.wav`

## Status
- `status` — whether the agent token is currently usable.

## Sending (NOT via narra_cli — use the dedicated tools)
- Reply to the current message: `narra_reply(text="...")`.
- Proactive text to a room: `narra_send(room_id, text)`.
- Image / file / audio / video: `narra_send_media(room_id, file_path, caption?)`.
