---
code_file: frontend/src/components/chat/team/index.ts
last_verified: 2026-07-30
stub: false
---

# team/index.ts — barrel for the team group-chat surface

[[MainLayout]] mounts `TeamChatPanel` and nothing else; the console
([[TeamActivityConsole]]) and the guide ([[TeamRoomGuide]]) are its parts and
are exported for tests, not for other screens to compose with. The package
exists because the surface reached three files (铁律 #23) — before the split
they were `Team*`-prefixed files sitting flat in `components/chat/`.
