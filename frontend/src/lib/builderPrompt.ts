/**
 * @file_name: builderPrompt.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The Agent Builder instruction that rides along with the
 * creation studio's first chat message, plus the draft-artifact convention
 * both sides agree on.
 *
 * v0 of the studio has no carrier agent: the conversation runs on the agent
 * that was just created, so builder behaviour cannot be injected as that
 * agent's Awareness — Awareness belongs to the user and v0 never writes it
 * unprompted. The instruction travels inside the first user message instead,
 * wrapped in markers that MessageBubble strips, so the user's own bubble
 * shows only what they typed.
 *
 * Markers are square-bracketed rather than XML-ish so they cannot collide
 * with tags a model emits in ordinary Markdown.
 *
 * The instruction names exactly ONE artifact title. The apply bar only
 * appears for that title, so a model that ignores the convention costs the
 * user a manual copy — never a wrong write into Awareness.
 */

import type { Artifact } from '@/types/artifact';

/** Opening marker of the instruction block. Stripped before render. */
export const BUILDER_MARK_OPEN = '[NARRANEXUS_AGENT_BUILDER_INSTRUCTION]';
/** Closing marker of the instruction block. */
export const BUILDER_MARK_CLOSE = '[/NARRANEXUS_AGENT_BUILDER_INSTRUCTION]';

/** Artifact title the builder must register the draft under. The apply bar
 *  keys off this exact value. */
export const DRAFT_ARTIFACT_TITLE = 'agent-config';
/** Workspace-relative entry file for the draft. */
export const DRAFT_ARTIFACT_FILE = 'agent-config.md';

/**
 * What the agent is told, once, in the first message.
 *
 * Every rule here maps to a v0 constraint in the PRD: produce a draft rather
 * than interrogate; one artifact, revised in place; never claim to have
 * changed configuration; no secrets in the draft.
 */
export const AGENT_BUILDER_INSTRUCTION = `You are acting as the NarraNexus Agent Builder for this conversation.
Help the user design ONE practical agent. Reply in the user's language.

Prefer producing a reasonable draft immediately over interrogating the user.
Ask at most two focused questions per turn, and only questions that materially
change behaviour. When the request is already complete, ask nothing.

Write the draft to \`${DRAFT_ARTIFACT_FILE}\` in your workspace, then call:
  register_artifact(entry_path="${DRAFT_ARTIFACT_FILE}", kind="text/markdown", title="${DRAFT_ARTIFACT_TITLE}")

To revise it, edit the same file and call register_artifact again with
target_artifact_id set to the existing artifact's id. Never register a second
artifact for this draft — the user must always have exactly one draft tab.

The draft is Markdown, in the user's language, and contains in this order:
1. A level-1 heading holding the proposed agent name (concise, suitable for a
   sidebar list).
2. One sentence describing what the agent does, at most 200 characters. It is
   read by OTHER AGENTS deciding whether to route work here, not by humans.
3. Sections for role, workflow, output and constraints — this part is the
   agent's future instructions, so write it as instructions, not as a report.
4. A final short section listing what the user still has to set up themselves
   (suggested skills, suggested channels), clearly marked as suggestions.

Hard rules:
- You are producing a DRAFT ONLY. You have not changed this agent's
  configuration and must never say you have. The user applies the draft with
  the button above it; say so if they ask how to make it take effect.
- Never request, expose, or place secrets, tokens, passwords, or environment
  variable values in the draft. Channels are named as intent only — the user
  supplies credentials themselves.
- Do not repeat the artifact URL in your reply; the tab is already visible.`;

/**
 * Assemble the studio's first chat message.
 *
 * Returns null for an empty request: the caller must not send an
 * instruction-only message, which would leave the agent guessing.
 */
export function buildBuilderFirstMessage(request: string): string | null {
  const trimmed = request.trim();
  if (!trimmed) return null;
  return [
    BUILDER_MARK_OPEN,
    AGENT_BUILDER_INSTRUCTION,
    BUILDER_MARK_CLOSE,
    '',
    trimmed,
  ].join('\n');
}

/**
 * Remove the instruction block from a message before rendering it.
 *
 * Safe to call on every message: content without the markers is returned
 * unchanged. An unterminated block (never produced by us, but cheap to
 * tolerate) is dropped through to the end so a half-written marker can't
 * leak the prompt into the bubble.
 */
export function stripBuilderInstruction(content: string): string {
  if (!content.includes(BUILDER_MARK_OPEN)) return content;
  const open = escapeForRegExp(BUILDER_MARK_OPEN);
  const close = escapeForRegExp(BUILDER_MARK_CLOSE);
  return content
    .replace(new RegExp(`${open}[\\s\\S]*?${close}`, 'g'), '')
    .replace(new RegExp(`${open}[\\s\\S]*$`), '')
    .trim();
}

function escapeForRegExp(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Whether an artifact is the studio's configuration draft.
 *
 * Lives here, next to the convention it tests, rather than beside the apply
 * button: the title and the check must move together, and a component file
 * cannot export helpers without breaking fast refresh.
 *
 * Deliberately strict. A model that titles its draft anything else gets no
 * apply button, which costs the user a manual copy — the alternative, a
 * looser match, would put a "write this into your instructions" button on
 * unrelated Markdown the agent happened to produce.
 */
export function isConfigDraft(artifact: Artifact | null | undefined): boolean {
  if (!artifact) return false;
  return artifact.kind === 'text/markdown' && artifact.title.trim() === DRAFT_ARTIFACT_TITLE;
}
