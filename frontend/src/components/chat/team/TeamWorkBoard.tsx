/**
 * @file_name: TeamWorkBoard.tsx
 * @author:
 * @date: 2026-08-10
 * @description: The team's work board — what is outstanding, and who has it.
 *
 * The roster answers "what is each member doing RIGHT NOW". This answers the
 * question that outlives a turn: "what did we agree to do, and is any of it
 * stuck". Before the board existed a task lived only inside the turn that
 * discussed it, so nothing on screen could show a flow silently dying.
 *
 * Two states here are the user's business specifically:
 *   - `stalled` is derived by the platform from real activity, so it can be
 *     shown as a fact rather than a guess;
 *   - `paused` is what stopping a run tree leaves behind. It is deliberately
 *     visible (a stopped task must not look deleted) and only the user can
 *     resume it — the Leader's patrol will not pick it back up on its own.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Play } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { TeamWorkItem } from '@/types/teams';

/** Poll with the transcript, so the board never lags the room it describes. */
const POLL_MS = 5000;

const STATUS_TONE: Record<string, string> = {
  open: 'var(--nm-ink50)',
  in_progress: 'var(--color-silicon)',
  stalled: 'var(--color-warning)',
  paused: 'var(--nm-ink30)',
};

export interface TeamWorkBoardProps {
  teamId: string;
  /** Shared 1s clock (epoch ms) — every duration on screen agrees. */
  now: number;
}

export function TeamWorkBoard({ teamId, now }: TeamWorkBoardProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<TeamWorkItem[]>([]);
  const [lastPatrolAt, setLastPatrolAt] = useState<string | null>(null);
  const [patrolEnabled, setPatrolEnabled] = useState(true);
  const [resuming, setResuming] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.getTeamWorkBoard(teamId);
      if (r.success) {
        setItems(r.items);
        setLastPatrolAt(r.last_patrol_at ?? null);
        setPatrolEnabled(r.patrol_enabled);
      }
    } catch {
      // transient — the next tick retries
    } finally {
      setLoaded(true);
    }
  }, [teamId]);

  useEffect(() => {
    let alive = true;
    setItems([]);
    setLoaded(false);
    refresh();
    const id = window.setInterval(() => { if (alive) refresh(); }, POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [refresh]);

  const resume = async (itemId: string) => {
    setResuming(itemId);
    try {
      await api.resumeTeamWorkItem(teamId, itemId);
      await refresh();
    } catch {
      // Leave the row parked; the user can try again.
    } finally {
      setResuming(null);
    }
  };

  if (!loaded) return null;
  // An empty board is the normal state for most teams — showing a header with
  // nothing under it would be permanent chrome for an absent thing.
  if (items.length === 0) return null;

  return (
    <div className="border-t border-[var(--border-subtle)] px-3 py-2">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span
          className="font-mono text-[10px] uppercase tracking-[0.18em]"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {t('chat.team.board.title')}
        </span>
        <span className="text-[10px] tabular-nums" style={{ color: 'var(--nm-ink30)' }}>
          {items.length}
        </span>
      </div>

      <div className="space-y-1">
        {items.map((item) => {
          const parked = item.status === 'paused';
          return (
            <div
              key={item.item_id}
              data-testid={`work-item-${item.item_id}`}
              className={cn('flex items-start gap-2 rounded px-1.5 py-1', parked && 'opacity-70')}
            >
              <span
                aria-hidden="true"
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: STATUS_TONE[item.status] || 'var(--nm-ink30)' }}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs" style={{ color: 'var(--nm-ink70)' }}>
                  {item.title}
                </div>
                <div className="font-mono text-[10px]" style={{ color: 'var(--nm-ink50)' }}>
                  {item.assignee_name || t('chat.team.board.unclaimed')}
                  {' · '}
                  {t(`chat.team.board.status.${item.status}`)}
                </div>
              </div>
              {/* Only the user resumes a parked task — patrol deliberately will
                  not, or stopping would undo itself on the next sweep. */}
              {parked && (
                <button
                  type="button"
                  onClick={() => resume(item.item_id)}
                  disabled={resuming === item.item_id}
                  data-testid={`work-resume-${item.item_id}`}
                  title={t('chat.team.board.resume')}
                  className="shrink-0 rounded p-1 text-[var(--nm-ink50)] transition-colors hover:bg-[var(--color-silicon)]/10 hover:text-[var(--color-silicon)]"
                >
                  {resuming === item.item_id ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Play className="h-3 w-3" />
                  )}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Patrol's trace. Shown rather than announced in the room: the sweep
          itself stays silent unless something is wrong, so this is the only
          place the user can see it is happening at all. */}
      <div className="mt-1.5 font-mono text-[10px]" style={{ color: 'var(--nm-ink30)' }}>
        {!patrolEnabled
          ? t('chat.team.board.patrolOff')
          : lastPatrolAt
            ? (_ago(lastPatrolAt, now)
                ? t('chat.team.board.patrolledAgo', { ago: _ago(lastPatrolAt, now) })
                : t('chat.team.board.patrolledJustNow'))
            : t('chat.team.board.patrolPending')}
      </div>
    </div>
  );
}

/** Elapsed label, or null when it rounds to "just now" — kept free of i18n so
 *  the caller owns every translated string. */
function _ago(iso: string, now: number): string | null {
  const ms = now - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 60000) return null;
  const mins = Math.floor(ms / 60000);
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
}
