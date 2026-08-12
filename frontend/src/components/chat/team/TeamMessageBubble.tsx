/**
 * @file_name: TeamMessageBubble.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: One message in a team room, rendered so a six-way conversation
 * can be read.
 *
 * Extracted from TeamChatPanel (876 lines) because this batch changes exactly
 * this: how one message looks. Only what this batch touches has been pulled
 * out — a wholesale reorganisation would be easier to review as its own change
 * and harder to review mixed with behaviour.
 *
 * Four things this fixes, each a way the room was unreadable or untruthful:
 *
 * **Identity.** Every agent shared one silicon colour and one avatar style; the
 * only difference between two speakers was 10px of grey text, which mobile
 * hides. Six agents were a uniform grey waterfall. The colour comes from
 * `senderIdentity`, seeded on the AGENT ID, so it is the same colour this agent
 * has in the inbox and the dashboard — an identity, not a decoration.
 *
 * **Length.** A long report ate the screen. Collapsed by default above a
 * threshold, matching the inbox's existing behaviour.
 *
 * **Deliberation vs answer.** With `include_monologue` on (team turns only) the
 * agent's thinking and its reply were concatenated into one markdown blob. They
 * are now laid out apart — but ONLY when the server recorded the boundary. A
 * message without segments renders as one block, which is what it did before:
 * guessing where the split was is worse than showing none, because a wrong
 * guess presents deliberation as a conclusion.
 *
 * **Mentions.** Being @mentioned was invisible in the body, so the person
 * addressed had to read every message to discover it was them. Only real
 * members (and @all) light up: highlighting anything after an @ would catch
 * email addresses and teach the reader to ignore the highlight.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Markdown } from '@/components/ui';
import { BusAttachmentList } from '../BusAttachmentList';
import { senderIdentity } from '@/lib/senderIdentity';
import { cn } from '@/lib/utils';
import type { TeamChatMessage } from '@/types/teams';

/** Above this many characters a message is a report, not a remark. Matches the
 *  inbox's existing threshold so the two surfaces fold at the same size. */
export const COLLAPSE_CHARS = 500;

export interface TeamMessageBubbleProps {
  message: TeamChatMessage;
  /** Display name for the human, for their own bubble. */
  userLabel: string;
  /** Which member is the lead, if any — shown as a badge. */
  leadAgentId?: string;
  /** agent_id → display name; the set a mention may resolve against. */
  memberNames: Record<string, string>;
  /** Rendered under the bubble (process disclosure, chips). */
  footer?: React.ReactNode;
}

interface Segment {
  kind: string;
  text: string;
}

/**
 * Split a body into text and mention runs.
 *
 * Resolves against the member list rather than the regex alone. The CJK range
 * here is the same one the composer uses; widening it is a separate fix that
 * has to move both at once, or the highlight and the send would disagree about
 * who was addressed.
 */
function markMentions(
  text: string,
  names: Set<string>,
): Array<{ text: string; mention?: string }> {
  const out: Array<{ text: string; mention?: string }> = [];
  const re = /@([\w一-鿿]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const word = m[1];
    const isAll = word.toLowerCase() === 'all' || word.toLowerCase() === 'everyone';
    if (!isAll && !names.has(word.toLowerCase())) continue;
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    out.push({ text: m[0], mention: word });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

/**
 * The same highlight for markdown bodies.
 *
 * Agent replies render through `Markdown`, so the highlight has to survive the
 * markdown pass — and an agent @mentioning a teammate is the handoff itself,
 * which is exactly when the addressee most needs to see it. `rehypeRaw` is
 * already enabled, so inline HTML renders; this changes no security posture
 * (model output has always been able to emit HTML there).
 *
 * The insertion is safe by construction rather than by escaping: the matched
 * run is `@` followed by word/CJK characters only, so nothing HTML-special can
 * reach the attribute or the body.
 */
function highlightMentionsInMarkdown(md: string, names: Set<string>): string {
  return md.replace(/@([\w一-鿿]+)/g, (whole, word: string) => {
    const lower = word.toLowerCase();
    const isAll = lower === 'all' || lower === 'everyone';
    if (!isAll && !names.has(lower)) return whole;
    return `<span data-testid="mention-${word}" class="rounded px-0.5 font-medium text-[var(--color-carbon)] bg-[var(--nm-paper-warm)]">${whole}</span>`;
  });
}

function MentionText({ text, names }: { text: string; names: Set<string> }) {
  const parts = useMemo(() => markMentions(text, names), [text, names]);
  return (
    <>
      {parts.map((p, i) =>
        p.mention ? (
          <span
            key={i}
            data-testid={`mention-${p.mention}`}
            className="rounded px-0.5 font-medium text-[var(--color-carbon)] bg-[var(--nm-paper-warm)]"
          >
            {p.text}
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

export function TeamMessageBubble({
  message: m,
  userLabel,
  leadAgentId = '',
  memberNames,
  footer,
}: TeamMessageBubbleProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const mine = m.is_user;
  const identity = senderIdentity(m.from_agent, mine ? userLabel : m.author_name);
  const isLead = !mine && !!leadAgentId && m.from_agent === leadAgentId;

  const nameSet = useMemo(
    () => new Set(Object.values(memberNames).map((n) => (n || '').toLowerCase())),
    [memberNames],
  );

  const body = (m.content || '').trim();
  const segments: Segment[] = Array.isArray(m.segments) && m.segments.length
    ? (m.segments as Segment[])
    : [];
  const tooLong = body.length > COLLAPSE_CHARS;
  const shown = tooLong && !expanded ? `${body.slice(0, COLLAPSE_CHARS)}…` : body;

  return (
    <div className={cn('flex gap-3', mine && 'flex-row-reverse')}>
      {/* The identity has to survive on mobile, where the name above the bubble
          is hidden — so it lives on the avatar as well as the bubble edge. */}
      <span
        data-testid={`avatar-${m.message_id}`}
        className={cn(
          'shrink-0 hidden md:inline-flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-mono text-white',
          mine ? 'bg-[var(--color-carbon)]' : identity.dot,
        )}
      >
        {identity.initials}
      </span>

      <div className={cn('flex-1 min-w-0', mine && 'text-right')}>
        {!mine && (
          <div className="mb-0.5 px-0.5 flex items-center gap-1.5 text-[10px] font-mono text-[var(--text-tertiary)]">
            <span>{m.author_name}</span>
            {isLead && (
              <span
                data-testid={`lead-badge-${m.message_id}`}
                className="rounded-full border border-[var(--border-subtle)] px-1 py-px"
              >
                {t('chat.team.leadBadge')}
              </span>
            )}
          </div>
        )}

        <div
          data-testid={`bubble-${m.message_id}`}
          className={cn(
            'relative inline-block max-w-[85%] text-left px-3.5 py-2.5 rounded-[var(--radius-lg)]',
            'border border-[var(--border-subtle)] border-l-[3px]',
            mine
              ? 'border-l-[var(--color-carbon)] bg-[var(--color-carbon-soft)]'
              : identity.accent,
          )}
        >
          <div className="text-sm break-words leading-relaxed">
            {mine ? (
              <span className="whitespace-pre-wrap">
                <MentionText text={shown} names={nameSet} />
              </span>
            ) : segments.length && !tooLong ? (
              segments.map((s, i) => (
                <div
                  key={i}
                  data-testid={`segment-${s.kind}-${i}`}
                  className={
                    s.kind === 'monologue'
                      ? 'mb-1.5 border-l-2 border-[var(--border-subtle)] pl-2 text-[0.8rem] italic text-[var(--text-tertiary)]'
                      : ''
                  }
                >
                  <Markdown content={highlightMentionsInMarkdown(s.text.trim(), nameSet)} />
                </div>
              ))
            ) : (
              <Markdown content={highlightMentionsInMarkdown(shown, nameSet)} />
            )}
          </div>

          {tooLong && (
            <button
              type="button"
              data-testid={`expand-${m.message_id}`}
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 text-[10px] font-mono text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
            >
              {expanded ? t('chat.team.collapse') : t('chat.team.expand')}
            </button>
          )}

          <BusAttachmentList attachments={m.attachments} />
          {footer}
        </div>
      </div>
    </div>
  );
}

export default TeamMessageBubble;
