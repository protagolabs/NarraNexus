/**
 * @file_name: index.ts
 * @author:
 * @date: 2026-07-28
 * @description: Barrel for the team group-chat surface.
 *
 * `TeamChatPanel` is the only member the rest of the app mounts; the console
 * and the guide are its parts and are exported for tests, not for other
 * screens to compose with.
 */

export { TeamChatPanel } from './TeamChatPanel';
export { TeamActivityBubble, TeamActivityConsole } from './TeamActivityConsole';
export { TeamRoomGuide } from './TeamRoomGuide';
