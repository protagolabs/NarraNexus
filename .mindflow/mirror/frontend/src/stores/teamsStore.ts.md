---
code_file: frontend/src/stores/teamsStore.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 巡查开关的单一副本 `patrolByTeam`

`patrolByTeam: Record<teamId, boolean>`(不持久化,是服务端状态)+ `notePatrol(teamId, enabled)`
(房间轮询 [[../components/chat/team/TeamChatPanel]] 与看板轮询 [[../components/chat/team/TeamWorkBoard]] 写入,
值不变时不触发 set)+ `setPatrol(teamId, enabled)`(乐观写 + `api.setTeamPatrol`,失败回滚到之前的值并抛出)。
消费方 [[../components/chat/team/TeamManagePanel]] 只读它。


## 2026-07-23 — deleteTeam tolerates 404

`deleteTeam` catches `ApiError` 404 (team already gone server-side) and
still runs `refresh()`. Rationale: the store is persisted to localStorage,
so a team deleted in another session kept resurrecting — deleting it again
hit 404, the throw skipped `refresh()`, and the stale cache could never be
purged (delete → 404 → still shown loop). Non-404 errors still rethrow
without refreshing. Tests: `__tests__/teamsStore.test.ts`.

# teamsStore.ts — Zustand store for subproject 1

State: `teams[]`, `loaded`, plus selector `teamsForAgent(agentId)`.

Actions: `refresh / createTeam / updateTeam / deleteTeam / addMember / removeMember` 都直调 `api.*` 然后 `await get().refresh()`。乐观更新没做 — 一律全量 refetch。规模小，简单胜过乐观更新。

`persist` middleware 缓存到 `narra-nexus-teams` localStorage key。
