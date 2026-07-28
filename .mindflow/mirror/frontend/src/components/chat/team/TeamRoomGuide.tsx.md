---
code_file: frontend/src/components/chat/team/TeamRoomGuide.tsx
last_verified: 2026-07-28
stub: false
---

# team/TeamRoomGuide.tsx — how this room is addressed

## Why it exists

A team room has addressing rules a 1:1 chat does not, and the old guidance was
both wrong and unreachable. It was a single grey placeholder line saying
"@mention a teammate to start the conversation" — but since the default-responder
change ([[teams]], 2026-07-21) an un-addressed message is NOT dropped, it goes to
the lead — and it was rendered only while `messages.length === 0`, so it vanished
for good after the first message.

## Design decisions

- States all three addressing modes explicitly, and **names** the default
  responder (`leadName`) rather than saying "the team lead" — "who answers if I
  just type" is the single most useful fact about a room you just opened. The
  member bar in [[TeamChatPanel]] marks the same agent with a dot.
- Mentions the cost of `@all` (every member runs a full turn) so broadcasting
  is an informed choice.
- Mentions the agent→agent relay cap so a hand-off chain stopping is not read
  as a bug.
- **Folding is a one-time decision**, persisted per team under
  `nx.team.guide.<teamId>`, and always reversible — the title row stays as a
  toggle. Not a banner the user re-closes on every visit.

## Gotchas

- Every `localStorage` access is wrapped: private mode / disabled storage must
  degrade to "show the guide", never break the toggle or the room.
