/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure:
 * ┌──────────┬─────────────────────────────┬──────────────────┐
 * │          │                             │ [Tab] [Tab] [Bell]│
 * │  Agent   │        Chat Area            ├──────────────────┤
 * │  List    │                             │                  │
 * │          │     (Spacious chat area)    │  Context Panel   │
 * │          │                             │  (Tab content)   │
 * └──────────┴─────────────────────────────┴──────────────────┘
 *
 * Right-side tabs: Runtime, Awareness, Agent Inbox, Jobs
 * Top-right bell: User Inbox Popover
 */

import { useState, useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { ContextPanelHeader, type ContextTab } from './ContextPanelHeader';
import { ContextPanelContent } from './ContextPanelContent';
import { ChatPanel } from '@/components/chat';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { useConfigStore, usePreloadStore } from '@/stores';
import { useAutoRefresh } from '@/hooks';

/** Default chat view with context panel */
export function ChatView() {
  const [contextTab, setContextTab] = useState<ContextTab>('runtime');
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });

  return (
    <main className="flex-1 flex min-w-0 p-4 gap-4 overflow-hidden relative z-10">
      {/* Chat Panel - Main area, takes up more space */}
      <div className="flex-[3] min-w-[400px] animate-fade-in">
        <ChatPanel onAgentComplete={refreshAll} />
      </div>

      {/* Context Panel - Right side panel */}
      <div className="flex-[2] min-w-[320px] flex flex-col animate-slide-in-right" style={{ animationDelay: '0.1s' }}>
        {/* Tab Header + User Inbox Bell */}
        <ContextPanelHeader
          activeTab={contextTab}
          onTabChange={setContextTab}
        />

        {/* Tab Content */}
        <ContextPanelContent activeTab={contextTab} />
      </div>
    </main>
  );
}

export function MainLayout() {
  const { agentId, userId } = useConfigStore();
  const { preloadAll } = usePreloadStore();
  const location = useLocation();

  // Check if we are rendering a sub-page (system, settings) vs. the chat view
  const isSubPage = location.pathname !== '/app/chat' && location.pathname !== '/app';

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  return (
    <div className="h-screen flex bg-[var(--bg-deep)] relative overflow-hidden">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Background agent completion toasts */}
      <AgentCompletionToast />

      {/* Render sub-page via Outlet, or the default chat view */}
      {isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          <Outlet />
        </main>
      ) : (
        <ChatView />
      )}
    </div>
  );
}

export default MainLayout;
