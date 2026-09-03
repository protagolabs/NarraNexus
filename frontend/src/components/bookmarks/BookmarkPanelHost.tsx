/**
 * @file_name: BookmarkPanelHost.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Renders the ONE panel behind an atomic bookmark tab.
 *
 * Every panel is React.lazy'd (the pattern the retired
 * ContextPanelContent used) so clicking a tab mounts exactly one light
 * chunk — heavy libraries (ReactFlow in jobs, markdown in awareness)
 * load on first use, not on app start, and never together. This is the
 * direct fix for the "small tabs respond slowly" feedback: the previous
 * drawer mounted Jobs + Inbox (or a whole accordion) statically.
 */

import { lazy, Suspense, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { useStudioStore, selectStudioOpen } from '@/stores/studioStore';
import { markTabOpened, type AtomicTabId } from './tabs';

const BuilderConfigPanel = lazy(() =>
  import('@/components/builder').then((m) => ({ default: m.BuilderConfigPanel })),
);
const AwarenessPanel = lazy(() =>
  import('@/components/awareness/AwarenessPanel').then((m) => ({ default: m.AwarenessPanel })),
);
const JobsPanel = lazy(() =>
  import('@/components/jobs/JobsPanel').then((m) => ({ default: m.JobsPanel })),
);
const AgentInboxPanel = lazy(() =>
  import('@/components/inbox/AgentInboxPanel').then((m) => ({ default: m.AgentInboxPanel })),
);
const SkillsPanel = lazy(() =>
  import('@/components/skills/SkillsPanel').then((m) => ({ default: m.SkillsPanel })),
);
const NarrativeList = lazy(() =>
  import('@/components/runtime/NarrativeList').then((m) => ({ default: m.NarrativeList })),
);
const ArtifactColumn = lazy(() =>
  import('@/components/artifacts').then((m) => ({ default: m.ArtifactColumn })),
);

function PanelFallback() {
  const { t } = useTranslation();
  return (
    <div className="h-full flex items-center justify-center py-16">
      <Loader2
        className="w-5 h-5 animate-spin"
        style={{ color: 'var(--text-tertiary)' }}
        aria-label={t('bookmarks.panelHost.loading')}
      />
    </div>
  );
}

export interface BookmarkPanelHostProps {
  tab: AtomicTabId;
  agentId: string;
}

export function BookmarkPanelHost({ tab, agentId }: BookmarkPanelHostProps) {
  // Opening a tab counts as seeing its news — clear its info highlights.
  useEffect(() => {
    markTabOpened(agentId, tab);
  }, [agentId, tab]);

  // The studio panel only renders while the studio is open on this agent.
  // The pickable lists already hide the tab, but a restored `drawerTab`, a
  // deep link, or the agent changing under an open drawer can still land
  // here — and a studio panel with no conversation driving it reads as
  // broken. Its guard is here, at the one place the panel is mounted.
  const studioOpen = useStudioStore(selectStudioOpen(agentId));

  const handleJobResolved = (jobId: string) => {
    useBookmarkStore.getState().resolveJobAttention(agentId, jobId);
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <Suspense fallback={<PanelFallback />}>
        {tab === 'builder' && studioOpen && <BuilderConfigPanel agentId={agentId} />}
        {tab === 'awareness' && <AwarenessPanel embedded section="awareness" />}
        {tab === 'workspace' && <AwarenessPanel embedded section="workspace" />}
        {tab === 'channels' && <AwarenessPanel embedded section="channels" />}
        {tab === 'smarthome' && <AwarenessPanel embedded section="smarthome" />}
        {tab === 'social' && <AwarenessPanel embedded section="social" />}
        {tab === 'jobs' && <JobsPanel embedded onJobResolved={handleJobResolved} />}
        {tab === 'inbox' && <AgentInboxPanel embedded />}
        {/* Artifacts — the retired side column rendered as a drawer panel;
            the drawer shell owns visibility (the column's sliver/collapse
            logic is gone). */}
        {tab === 'artifacts' && <ArtifactColumn agentId={agentId} />}
        {tab === 'skills' && <SkillsPanel embedded section="skills" />}
        {tab === 'mcp' && <SkillsPanel embedded section="mcp" />}
        {tab === 'memory' && (
          <div className="flex-1 min-h-0 overflow-y-auto px-1 py-2">
            <NarrativeList />
          </div>
        )}
      </Suspense>
    </div>
  );
}
