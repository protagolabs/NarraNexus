/**
 * @file_name: MainLayout.tsx
 * @author: Bin Liang
 * @date: 2025-01-15
 * @description: Main Layout - Bioluminescent Terminal Style
 *
 * Layout structure (Chat UI v4):
 * ┌──────────┬──────────────────────────────────┬──────────────────┐
 * │  Sidebar │            Chat Area             │ Bookmark Drawer  │
 * │  (Agent  │  (full-bleed, header owns all    │ (pinned column / │
 * │   List)  │   panel + artifacts entries)     │  slide-over)     │
 * └──────────┴──────────────────────────────────┴──────────────────┘
 *
 * Panel entries (Awareness / Jobs / Inbox / Artifacts / …) live in the chat
 * header (v4); they funnel through uiStore.requestPanel into the single
 * BookmarkDrawer below. The old right-edge BookmarkStrip AND the resizable
 * side Artifact Column are retired — artifacts render as a drawer panel
 * like everything else (Owner 2026-08-06). The pinned drawer keeps its own
 * ResizableDivider + persisted width.
 *
 * Signal source: artifact_id signals arrive via the chat WebSocket stream
 * (tool_output frames parsed in ChatPanel.tsx). loadPinned is called on mount /
 * agent change to hydrate agent-scoped artifacts (feeds the header badge and
 * the drawer panel). No dedicated artifact WS.
 */

import { useState, useEffect, useRef, useCallback, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { X, PanelLeft } from 'lucide-react';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { CommandPalette } from './CommandPalette';
import { DashboardSkeleton } from '@/components/dashboard/DashboardSkeleton';
import { ResizableDivider } from './ResizableDivider';
import {
  BookmarkDrawer,
  BookmarkPanelHost,
  tabLabelKey,
  tabDescKey,
} from '@/components/bookmarks';
import type { AtomicTabId } from '@/components/bookmarks';
import { HelpButton, CHAT_VIEW_PAGES } from '@/components/help';
import { FeedbackButton } from '@/components/ui/FeedbackButton';
import { useBookmarkSignals } from '@/hooks/useBookmarkSignals';
import { ChatPanel } from '@/components/chat';
import { WakingOverlay } from '@/components/chat/WakingOverlay';
import { TeamChatPanel } from '@/components/chat/team';
import { CostPopover } from '@/components/cost/CostPopover';
import { OnboardingChecklist } from '@/components/onboarding/OnboardingChecklist';
import { MigrationGuide } from '@/components/onboarding/MigrationGuide';
import { AgentCompletionToast } from '@/components/ui/AgentCompletionToast';
import { useConfigStore, usePreloadStore, useArtifactStore, useUIStore } from '@/stores';
import { useIsMobile } from '@/hooks/useMediaQuery';
import { useAutoRefresh } from '@/hooks';

const DRAWER_PINNED_KEY = 'bookmark_drawer_pinned_v1';
const DRAWER_OPENED_ONCE_KEY = 'bookmark_drawer_opened_v1';
const DRAWER_WIDTH_KEY = 'bookmark_drawer_width_v1';
// Pinned bookmark drawer: user-resizable like the chat ↔ artifacts split, so
// every column on the right side follows the same "grab the rule and drag"
// rule instead of one of them being a hardcoded width.
const DEFAULT_DRAWER_PX = 400;
const MIN_DRAWER_PX = 300;
const MAX_DRAWER_PX = 720;

function readInitialDrawerWidth(): number {
  if (typeof window === 'undefined') return DEFAULT_DRAWER_PX;
  const raw = window.localStorage.getItem(DRAWER_WIDTH_KEY);
  if (!raw) return DEFAULT_DRAWER_PX;
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_DRAWER_PX;
  return Math.min(MAX_DRAWER_PX, Math.max(MIN_DRAWER_PX, parsed));
}

/** Default chat view with context panel */
export function ChatView() {
  // Bookmark drawer: which atomic tab is open (null = closed) and whether
  // the drawer is pinned into a static column (persisted — pinning is a
  // deliberate workspace choice). One tab = one panel (Owner IA).
  const { t: tr } = useTranslation();
  const [drawerTab, setDrawerTab] = useState<AtomicTabId | null>(null);
  const [drawerPinned, setDrawerPinned] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem(DRAWER_PINNED_KEY) === '1';
  });
  const { agentId, userId } = useConfigStore();
  const { refreshAll } = useAutoRefresh({ agentId, userId });
  useBookmarkSignals(agentId);

  const isMobile = useIsMobile();
  const pendingPanel = useUIStore((s) => s.pendingPanel);
  const clearPendingPanel = useUIStore((s) => s.clearPendingPanel);
  const requestPanel = useUIStore((s) => s.requestPanel);

  const handleDrawerClose = () => setDrawerTab(null);

  // A panel requested from the chat header entries / ⋯ detail menu / the
  // command palette (all funnel through uiStore.requestPanel — the strip
  // that used to own this is retired in v4). Re-requesting the open tab
  // closes the drawer (toggle), matching the old strip behavior.
  useEffect(() => {
    if (pendingPanel) {
      setDrawerTab((prev) => {
        if (prev === (pendingPanel as AtomicTabId)) return null;
        try {
          window.localStorage.setItem(DRAWER_OPENED_ONCE_KEY, '1');
        } catch { /* storage unavailable — onboarding hint just stays */ }
        return pendingPanel as AtomicTabId;
      });
      clearPendingPanel();
    }
  }, [pendingPanel, clearPendingPanel]);

  const handlePinnedChange = (pinned: boolean) => {
    setDrawerPinned(pinned);
    try {
      window.localStorage.setItem(DRAWER_PINNED_KEY, pinned ? '1' : '0');
    } catch { /* non-fatal */ }
  };

  const loadPinned = useArtifactStore((s) => s.loadPinned);
  const artifactsLength = useArtifactStore((s) => s.artifacts.length);

  // Pinned-drawer width — two-phase drag (imperative during, committed +
  // persisted on release). No iframe lives in the drawer, so there is
  // nothing to freeze here.
  const [drawerWidth, setDrawerWidth] = useState<number>(() => readInitialDrawerWidth());
  const drawerColRef = useRef<HTMLDivElement | null>(null);
  const pendingDrawerWidthRef = useRef<number>(drawerWidth);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(DRAWER_WIDTH_KEY, String(drawerWidth));
  }, [drawerWidth]);

  // Pinned-drawer drag. Width grows leftward from the drawer's own right
  // edge, so the edge stays put while the chat column absorbs the change.
  const computeDrawerWidth = useCallback((clientX: number): number | null => {
    const el = drawerColRef.current;
    if (!el) return null;
    const right = el.getBoundingClientRect().right;
    return Math.min(MAX_DRAWER_PX, Math.max(MIN_DRAWER_PX, right - clientX));
  }, []);

  const handleDrawerResize = useCallback((clientX: number) => {
    const el = drawerColRef.current;
    const width = computeDrawerWidth(clientX);
    if (!el || width === null) return;
    pendingDrawerWidthRef.current = width;
    el.style.width = `${width}px`;
  }, [computeDrawerWidth]);

  const handleDrawerResizeEnd = useCallback((clientX: number) => {
    const width = computeDrawerWidth(clientX);
    if (width !== null) pendingDrawerWidthRef.current = width;
    setDrawerWidth(pendingDrawerWidthRef.current);
  }, [computeDrawerWidth]);

  // Load pinned artifacts whenever agentId changes — even with the side
  // column retired, the header badge count and the drawer panel both read
  // from this hydration.
  // Note: chatStore does not expose a per-agent session ID, so loadForSession
  // is not called here. Session-scoped artifacts arrive via the chat WS stream
  // (tool_output frames parsed in ChatPanel.tsx).
  // TODO: if chatStore gains a sessionId field, add loadForSession(agentId, sessionId) here.
  useEffect(() => {
    if (!agentId) return;
    loadPinned(agentId);
  }, [agentId, loadPinned]);

  return (
    // v4: full-bleed, seam-free — the chat surface runs edge to edge with
    // hairline separations instead of padded floating cards. Artifacts no
    // longer occupy a side column: they open in the bookmark drawer via the
    // chat header's Artifacts entry (Owner 2026-08-06).
    <main className="flex-1 flex min-w-0 overflow-hidden relative z-10">
      <div className="relative flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Mobile utility row — artifacts entry + cost chip (the desktop
            header doesn't render on < md; this row keeps both one tap away). */}
        {isMobile && agentId && (
          <div className="flex h-9 shrink-0 items-center justify-end gap-1 px-1.5 border-b border-[var(--nm-hairline)]">
            <button
              type="button"
              onClick={() => requestPanel('artifacts')}
              className="inline-flex items-center gap-1.5 px-2 h-7 rounded-[var(--radius-sm)] font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] transition-colors hover:text-[var(--nm-ink)]"
            >
              {artifactsLength > 0
                ? tr('layout.chatView.tabArtifactsCount', { count: artifactsLength })
                : tr('layout.chatView.tabArtifacts')}
            </button>
            <CostPopover compact />
          </div>
        )}

        {/* Chat column — NM paper card (the actual conversation surface,
            --nm-card sits on top of the warm nm-paper background).
            flex-col so the (cloud-only, self-hiding) onboarding checklist
            can sit above the chat without ChatPanel losing its height. */}
        <div
          className="min-w-0 flex-1 animate-fade-in overflow-hidden flex flex-col"
          style={{ background: 'var(--nm-card)' }}
        >
          <OnboardingChecklist />
          <MigrationGuide />
          <div className="relative flex-1 min-h-0">
            <ChatPanel onAgentComplete={refreshAll} />
            <WakingOverlay />
          </div>
        </div>
      </div>

      {/* Drag handle for the pinned drawer's width. */}
      {drawerPinned && drawerTab && agentId && !isMobile && (
        <ResizableDivider
          onResize={handleDrawerResize}
          onResizeEnd={handleDrawerResizeEnd}
          label={tr('layout.resizableDivider.drawerAriaLabel')}
          title={tr('layout.resizableDivider.drawerTitle')}
        />
      )}

      {/* The bookmark drawer — ONE element for both modes, deliberately.
          Pinned it renders here as a static column; unpinned it portals out to
          body as a slide-over. Keeping it a single element at a single position
          in the React tree is what stops a pin/unpin toggle from unmounting the
          panel and silently discarding everything the user had set up inside it
          (filters, view mode, expanded rows). Do NOT split this back into two
          conditional <BookmarkDrawer> elements. */}
      {agentId && (
        <BookmarkDrawer
          open={drawerTab !== null}
          pinned={drawerPinned}
          onPinnedChange={handlePinnedChange}
          onClose={handleDrawerClose}
          title={drawerTab ? tr(tabLabelKey(drawerTab)) : ''}
          description={drawerTab ? tr(tabDescKey(drawerTab), '') : ''}
          edgeReservePx={0}
          pinnedWidth={drawerWidth}
          // Desktop: transient drawer is an in-flow column (chat shifts left,
          // nothing gets covered). Artifacts wants big-screen readability —
          // ~half the viewport, clamped so the sidebar (272) + chat (400)
          // always keep their minimum room; other panels stay at 440px.
          inset={!isMobile}
          insetWidth={
            drawerTab === 'artifacts'
              ? 'min(max(440px, 50vw), calc(100vw - 672px))'
              : 440
          }
          columnRef={drawerColRef}
        >
          {drawerTab && <BookmarkPanelHost tab={drawerTab} agentId={agentId} />}
        </BookmarkDrawer>
      )}

      {/* The right-edge bookmark strip is retired in v4 — every panel entry
          now lives in the chat header (icons + ⋯ detail menu), all funneling
          through uiStore.requestPanel into the same drawer above. */}

      {/* Hand-annotated page guide — bottom-left ?, spec §12 */}
      {/* Floating help (?) — desktop only; on mobile the bottom-right corner
          is reserved for content and the page guide isn't tuned for touch. */}
      {!isMobile && <HelpButton pages={CHAT_VIEW_PAGES} />}

    </main>
  );
}

/**
 * Team group-chat view. Occupies the same main slot as ChatView so
 * switching between a single agent and a team feels seamless (no
 * sub-page overlay / close-X). Artifacts + bookmarks are intentionally
 * omitted for now — the shared room is the focus.
 */
export function TeamChatView({ teamId }: { teamId: string }) {
  return (
    <main className="flex-1 flex min-w-0 overflow-hidden relative z-10">
      <div
        className="flex-1 min-w-0 animate-fade-in overflow-hidden flex flex-col"
        style={{ background: 'var(--nm-card)' }}
      >
        <TeamChatPanel teamId={teamId} />
      </div>
    </main>
  );
}

export function MainLayout() {
  const { t } = useTranslation();
  const { agentId, userId } = useConfigStore();
  const { preloadAll } = usePreloadStore();
  const location = useLocation();
  const navigate = useNavigate();
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const paletteOpen = useUIStore((s) => s.paletteOpen);
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen);
  const isMobile = useIsMobile();

  // Global ⌘K / Ctrl+K — hosted here (not the mobile-only TopBar) so the
  // palette works on every viewport and route.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        const { paletteOpen: open, setPaletteOpen: setOpen } = useUIStore.getState();
        setOpen(!open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Close the mobile sidebar drawer whenever the view changes (picked an agent
  // or navigated to a sub-page) so the user lands on the content they tapped.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, agentId, setMobileNavOpen]);

  // Team group chat (`/app/teams/:teamId/chat`) renders in the main slot like
  // the chat view — a seamless switch between a single agent and a team, NOT a
  // sub-page overlay with a close-X.
  const teamChatMatch = location.pathname.match(/^\/app\/teams\/([^/]+)\/chat$/);
  const teamChatId = teamChatMatch ? teamChatMatch[1] : null;

  // Check if we are rendering a sub-page (system, settings) vs. the chat view
  const isSubPage =
    !teamChatId &&
    location.pathname !== '/app/chat' &&
    location.pathname !== '/app';

  // Preload all data when component mounts or when agentId/userId changes
  useEffect(() => {
    if (agentId && userId) {
      preloadAll(agentId, userId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, userId]);

  return (
    // h-dvh-safe (not h-screen): 100vh on mobile includes the space behind
    // the browser's retractable UI, pushing the layout's bottom edge under
    // the toolbar. The class carries a plain-vh fallback for engines
    // without dvh (see index.css).
    <div className="h-dvh-safe flex flex-col bg-[var(--bg-deep)] relative overflow-hidden">
      {/* Mobile-only status strip — hamburger + breadcrumb + ⌘K. Renders
          nothing on md+ (v4: the sidebar owns the full height there). */}
      <TopBar />

      {/* Command palette — one instance for all viewports and routes. */}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      <div className="flex flex-1 min-h-0 relative">
      {/* Sidebar - Agent List */}
      <Sidebar />

      {/* Collapsed-sidebar expand rail. The chat view renders its own inline
          expand button in the chat header (v4); sub-pages and team chat get
          a slim reserved rail instead of a floating chip — the page content
          shifts right, so nothing is ever covered (Owner 2026-08-06). */}
      {!isMobile && sidebarCollapsed && (isSubPage || teamChatId) && (
        <div className="shrink-0 flex w-11 flex-col items-center border-r border-[var(--nm-hairline)] bg-[var(--nm-paper)] pt-3">
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            title={t('sidebar.expandTitle')}
            aria-label={t('sidebar.expandTitle')}
            className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink50)] transition-colors hover:bg-[var(--nm-raised)] hover:text-[var(--nm-ink)]"
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Mobile drawer backdrop — taps to close the off-canvas sidebar.
          Sits below the sidebar (z-40) and above the content (z-10). */}
      {mobileNavOpen && (
        <div
          className="fixed inset-x-0 bottom-0 top-9 z-30 bg-[var(--nm-backdrop)] md:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden
        />
      )}

      {/* Background agent completion toasts */}
      <AgentCompletionToast />

      {/* Feedback entry — every desktop route, not just the chat view (the
          sidebar-footer entry it replaced was global too). It occupies the
          corner slot when there's no "?" (sub-pages) and stacks above it on
          the chat view. Mobile keeps its entry in the sidebar drawer footer:
          the corner belongs to the composer there. */}
      {!isMobile && <FeedbackButton aboveHelp={!isSubPage && !teamChatId} />}

      {/* Render: team group chat, a sub-page via Outlet, or the chat view */}
      {teamChatId ? (
        <TeamChatView teamId={teamChatId} />
      ) : isSubPage ? (
        <main className="flex-1 min-w-0 overflow-hidden relative z-10">
          {/* Close button — sub-pages (Dashboard / Settings / System …) open
              over the chat with no obvious way back, so dock an X top-right
              that returns to the conversation. */}
          <button
            type="button"
            onClick={() => navigate('/app/chat')}
            title={t('layout.subPage.closeTitle')}
            aria-label={t('layout.subPage.closeAriaLabel')}
            className="absolute top-4 right-4 z-30 flex h-6 w-6 items-center justify-center rounded-[var(--radius-sm)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
          >
            <X className="h-3.5 w-3.5" />
          </button>
          {/* v2.2 G1: inner Suspense so lazy sub-pages (DashboardPage etc.)
              don't trigger the App-level full-screen spinner that hides the
              Sidebar. The skeleton mirrors the dashboard grid shape. */}
          <Suspense fallback={<DashboardSkeleton />}>
            <Outlet />
          </Suspense>
        </main>
      ) : (
        <ChatView />
      )}
      </div>
    </div>
  );
}

export default MainLayout;
