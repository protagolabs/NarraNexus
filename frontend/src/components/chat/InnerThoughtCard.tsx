/**
 * @file_name: InnerThoughtCard.tsx
 * @author: Bin Liang
 * @date: 2026-07-03
 * @description: One "inner thought" (message_type=activity) as an expandable card.
 *
 * An activity row is written whenever a NON-chat trigger runs the agent and
 * the agent sent no user-facing reply (chat_module.py). Those triggers are
 * diverse — a scheduled job, an agent-to-agent bus message, an inbound IM on
 * any channel, a skill study — so the card is headed by its
 * ``item.workingSource`` (icon + localized source name) rather than a flat
 * "Background activity" line.
 *
 * What the agent actually did that turn lives in the events table and is
 * fetched lazily by ``item.eventId`` via ``api.getEventLog`` (the same
 * endpoint + shape MessageBubble uses for reasoning) — only on first expand,
 * cached in component state. The event log's ``timeline`` (thinking /
 * tool_call / tool_output / native_output / reply) renders as a compact step
 * list; it falls back to (thinking, tool_calls) for old backends, shows an
 * empty state when the log carries nothing, distinguishes a load FAILURE from
 * a genuinely empty log, and shows no expander at all when there is no
 * event_id.
 */

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight, ChevronDown, Loader2,
  Brain, Wrench, Users, MessageCircle, Sparkles, CheckCircle2, TerminalSquare,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { TimelineItem } from '@/lib/buildTimeline';
import type { EventLogResponse, EventLogTimelineEntry } from '@/types/api';

interface SourceMeta {
  Icon: typeof Brain;
  labelKey: string;
}

// working_source → header icon + i18n label. IM channels share one bubble
// icon; unknown/other sources fall back to the generic "activity" label.
const SOURCE_META: Record<string, SourceMeta> = {
  job: { Icon: Wrench, labelKey: 'chat.inner.source.job' },
  message_bus: { Icon: Users, labelKey: 'chat.inner.source.collaboration' },
  a2a: { Icon: Users, labelKey: 'chat.inner.source.collaboration' },
  lark: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  slack: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  telegram: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  wechat: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  discord: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  narramessenger: { Icon: MessageCircle, labelKey: 'chat.inner.source.im' },
  skill_study: { Icon: Sparkles, labelKey: 'chat.inner.source.skill' },
};
const DEFAULT_META: SourceMeta = { Icon: Brain, labelKey: 'chat.inner.source.activity' };

type LoadState = 'idle' | 'loading' | 'ok' | 'error';

/** Prefer the structured timeline; fall back to legacy thinking + tool_calls. */
function toEntries(res: EventLogResponse): EventLogTimelineEntry[] {
  if (res.timeline && res.timeline.length) return res.timeline;
  const out: EventLogTimelineEntry[] = [];
  if (res.thinking) out.push({ type: 'thinking', content: res.thinking });
  for (const tc of res.tool_calls ?? []) {
    out.push({
      type: 'tool_call',
      tool_name: tc.tool_name,
      tool_input: tc.tool_input,
      tool_output: tc.tool_output,
    });
  }
  return out;
}

function EntryRow({ entry }: { entry: EventLogTimelineEntry }) {
  if (entry.type === 'tool_call') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <Wrench className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        <span className="font-mono break-all">{entry.tool_name}</span>
      </div>
    );
  }
  if (entry.type === 'tool_output') {
    return (
      <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
        <TerminalSquare className="w-3 h-3 mt-0.5 shrink-0" />
        <span className="break-words font-mono">{entry.content ?? entry.tool_output}</span>
      </div>
    );
  }
  if (entry.type === 'reply') {
    return (
      <div className="flex items-start gap-1.5 text-xs">
        <CheckCircle2 className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--status-success, #2e7d32)' }} />
        <span className="break-words">{entry.content}</span>
      </div>
    );
  }
  // thinking / native_output — plain text, dimmed
  return (
    <div className="flex items-start gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
      <Brain className="w-3 h-3 mt-0.5 shrink-0" />
      <span className="break-words italic">{entry.content}</span>
    </div>
  );
}

export function InnerThoughtCard({ item, agentId }: { item: TimelineItem; agentId: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [state, setState] = useState<LoadState>('idle');
  const [entries, setEntries] = useState<EventLogTimelineEntry[]>([]);

  const canExpand = !!item.eventId;
  const meta = SOURCE_META[item.workingSource ?? ''] ?? DEFAULT_META;
  const { Icon } = meta;

  const toggle = useCallback(async () => {
    const next = !expanded;
    setExpanded(next);
    // Fetch once, on first successful-or-pending expand. 'loading' guards
    // against a double-fetch when the user toggles rapidly mid-flight.
    if (next && (state === 'idle' || state === 'error') && item.eventId) {
      setState('loading');
      try {
        const res = await api.getEventLog(agentId, item.eventId);
        setEntries(toEntries(res));
        setState('ok');
      } catch {
        setEntries([]);
        setState('error');
      }
    }
  }, [expanded, state, item.eventId, agentId]);

  return (
    <div
      data-testid="inner-thought-card"
      className="mx-3 my-1.5 rounded-lg border px-3 py-2"
      style={{ borderColor: 'var(--border-subtle, #e5e5e5)' }}
    >
      <div className="flex items-center gap-2">
        <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-tertiary)' }} />
        <span className="text-xs font-medium">{t(meta.labelKey)}</span>
        <span className="text-[10px] ml-auto" style={{ color: 'var(--text-tertiary)' }}>
          {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>

      <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
        {item.content}
      </div>

      {canExpand && (
        <button
          type="button"
          onClick={toggle}
          className="mt-1.5 flex items-center gap-1 text-[11px]"
          style={{ color: 'var(--text-tertiary)' }}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          {t('chat.inner.viewLoop')}
        </button>
      )}

      {expanded && (
        <div className="mt-2 space-y-1.5 pl-1 border-l" style={{ borderColor: 'var(--border-subtle, #e5e5e5)' }}>
          {state === 'loading' ? (
            <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-tertiary)' }}>
              <Loader2 className="w-3 h-3 animate-spin" />
              {t('chat.inner.loading')}
            </div>
          ) : state === 'error' ? (
            <div className="text-xs" style={{ color: 'var(--status-error, #c0392b)' }}>
              {t('chat.inner.loadFailed')}
            </div>
          ) : entries.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              {t('chat.inner.empty')}
            </div>
          ) : (
            entries.map((e, i) => <EntryRow key={i} entry={e} />)
          )}
        </div>
      )}
    </div>
  );
}
