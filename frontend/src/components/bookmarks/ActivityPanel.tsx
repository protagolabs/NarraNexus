/**
 * @file_name: ActivityPanel.tsx
 * @author:
 * @date: 2026-06-10
 * @description: "Activity" drawer panel — the merged answer to "what is /
 * was my agent doing". Two stacked sections (Jobs, Inbox; NOT interleaved,
 * spec §14.1) reusing the existing JobsPanel / AgentInboxPanel in embedded
 * mode. Deep-links from small bookmarks scroll to the matching section and
 * clear that bookmark's info highlight via markOpened.
 */

import { useEffect, useRef } from 'react';
import { BracketSectionLabel } from '@/components/nm';
import { JobsPanel } from '@/components/jobs/JobsPanel';
import { AgentInboxPanel } from '@/components/inbox/AgentInboxPanel';
import { useBookmarkStore } from '@/stores';

export interface ActivityPanelProps {
  agentId: string;
  /** Bookmark key that opened the drawer: 'job:<id>' | 'inbox'. */
  focusKey?: string;
}

export function ActivityPanel({ agentId, focusKey }: ActivityPanelProps) {
  const jobsRef = useRef<HTMLDivElement | null>(null);
  const inboxRef = useRef<HTMLDivElement | null>(null);

  // Deep-link: scroll the relevant section into view and clear the
  // bookmark's info highlight. Section-level granularity for v1 —
  // row-level focus needs JobsPanel to accept an external expandedId.
  useEffect(() => {
    if (!focusKey) return;
    const target = focusKey.startsWith('job:') ? jobsRef.current : inboxRef.current;
    target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    useBookmarkStore.getState().markOpened(agentId, focusKey);
  }, [agentId, focusKey]);

  const handleJobResolved = (jobId: string) => {
    useBookmarkStore.getState().resolveJobAttention(agentId, jobId);
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto">
      {/* ── Jobs ── */}
      <section ref={jobsRef} className="flex flex-col min-h-0 shrink-0">
        <div className="px-4 pt-3 pb-1">
          <BracketSectionLabel>Jobs</BracketSectionLabel>
        </div>
        <div className="min-h-0 max-h-[55vh] flex flex-col">
          <JobsPanel embedded onJobResolved={handleJobResolved} />
        </div>
      </section>

      <div className="mx-4 my-2 border-t border-[var(--nm-hairline)]" aria-hidden />

      {/* ── Inbox ── */}
      <section ref={inboxRef} className="flex flex-col min-h-0 shrink-0">
        <div className="px-4 pt-1 pb-1">
          <BracketSectionLabel>Inbox</BracketSectionLabel>
        </div>
        <div className="min-h-0 max-h-[55vh] flex flex-col">
          <AgentInboxPanel embedded />
        </div>
      </section>
    </div>
  );
}
