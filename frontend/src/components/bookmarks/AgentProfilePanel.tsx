/**
 * @file_name: AgentProfilePanel.tsx
 * @author:
 * @date: 2026-06-10
 * @description: "Agent" drawer panel — everything about who this agent is.
 * Single-open accordion: Awareness (incl. Workspace / IM Channels / Social
 * inside AwarenessPanel), Skills & MCP, Memory (NarrativeList). Default-open
 * follows the section carrying a profile:* highlight so the drawer lands on
 * what changed; opening a highlighted section clears it via markOpened.
 */

import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { AwarenessPanel } from '@/components/awareness/AwarenessPanel';
import { SkillsPanel } from '@/components/skills/SkillsPanel';
import { NarrativeList } from '@/components/runtime/NarrativeList';
import { useBookmarkStore } from '@/stores';
import { cn } from '@/lib/utils';

export interface AgentProfilePanelProps {
  agentId: string;
  /** Bookmark key that opened the drawer: 'profile:<section>'. */
  focusKey?: string;
}

type SectionId = 'awareness' | 'skills' | 'memory';

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: 'awareness', label: 'Awareness' },
  { id: 'skills', label: 'Skills & MCP' },
  { id: 'memory', label: 'Memory' },
];

function sectionFromKey(key: string | undefined): SectionId | null {
  if (!key?.startsWith('profile:')) return null;
  const section = key.slice('profile:'.length);
  return SECTIONS.some((s) => s.id === section) ? (section as SectionId) : null;
}

const EMPTY_HIGHLIGHTS: Record<string, 'attention' | 'info'> = {};

export function AgentProfilePanel({ agentId, focusKey }: AgentProfilePanelProps) {
  // Select the stored slice only — falling back to a NEW object inside the
  // selector would change the snapshot identity every call and loop the
  // useSyncExternalStore subscription.
  const agentSlice = useBookmarkStore((s) => s.agents[agentId]);
  const highlights = agentSlice?.highlights ?? EMPTY_HIGHLIGHTS;

  // Default-open priority: explicit focusKey > highlighted section > first.
  const [open, setOpen] = useState<SectionId>(() => {
    const fromFocus = sectionFromKey(focusKey);
    if (fromFocus) return fromFocus;
    const highlighted = SECTIONS.find((s) => highlights[`profile:${s.id}`]);
    return highlighted?.id ?? 'awareness';
  });

  // Deep-link mount: a focusKey that points at a highlighted section
  // counts as the user having looked at it — clear its info highlight.
  useEffect(() => {
    if (!focusKey) return;
    const section = sectionFromKey(focusKey);
    if (section && highlights[focusKey]) {
      useBookmarkStore.getState().markOpened(agentId, focusKey);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-time deep-link only
  }, []);

  const handleToggle = (id: SectionId) => {
    setOpen(id);
    const key = `profile:${id}`;
    if (highlights[key]) {
      useBookmarkStore.getState().markOpened(agentId, key);
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto">
      {SECTIONS.map(({ id, label }) => {
        const isOpen = open === id;
        const hasUpdate = !!highlights[`profile:${id}`];
        return (
          <section key={id} className="flex flex-col min-h-0 shrink-0">
            <button
              onClick={() => handleToggle(id)}
              aria-expanded={isOpen}
              className={cn(
                'flex items-center gap-2 w-full px-4 py-2.5 text-left',
                'border-b border-[var(--nm-hairline)]',
                'transition-colors hover:bg-[var(--nm-paper-warm)]',
              )}
            >
              <ChevronRight
                className={cn(
                  'w-3 h-3 shrink-0 transition-transform duration-150',
                  isOpen && 'rotate-90',
                )}
                style={{ color: 'var(--nm-ink30)' }}
              />
              <span
                className="flex-1 text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em]"
                style={{ color: isOpen ? 'var(--nm-ink)' : 'var(--nm-ink50)' }}
              >
                {label}
              </span>
              {hasUpdate && (
                <span
                  className="w-1.5 h-1.5 rounded-full allow-circle shrink-0"
                  style={{ background: 'var(--color-yellow-500)' }}
                  aria-label="Updated"
                />
              )}
            </button>
            {isOpen && (
              <div className="min-h-0 max-h-[70vh] flex flex-col">
                {id === 'awareness' && <AwarenessPanel embedded />}
                {id === 'skills' && <SkillsPanel embedded />}
                {id === 'memory' && (
                  <div className="overflow-y-auto px-1 py-2">
                    <NarrativeList />
                  </div>
                )}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
