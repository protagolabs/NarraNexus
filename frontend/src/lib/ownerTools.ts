/**
 * Which tool calls are the agent speaking to its owner.
 *
 * There are two names for one destination — `reply_owner` when the owner is
 * the one who spoke and is waiting, `notify_owner` when the owner is not part
 * of what is happening and is being interrupted. The agent's desk carries
 * exactly one per turn (see ChatModule.get_expressive_tools on the backend),
 * so the frontend can never assume which one a given turn produced.
 *
 * Anything that answers "did the owner receive something" must accept BOTH.
 * Matching one name only was the pre-split shape of this check and it is
 * silently wrong on every turn that used the other: the reply is real, the
 * content is there, and the bubble simply never renders.
 *
 * Mirrors `_OWNER_TOOL_RE` in
 * `src/xyz_agent_context/module/chat_module/chat_module.py` — the two must
 * move together.
 */
const OWNER_TOOL_RE = /(?:reply|notify)_owner$/;

/** True for `reply_owner` / `notify_owner`, bare or `mcp__<server>__`-prefixed. */
export function isOwnerReplyTool(toolName: string | undefined | null): boolean {
  return !!toolName && OWNER_TOOL_RE.test(toolName);
}
