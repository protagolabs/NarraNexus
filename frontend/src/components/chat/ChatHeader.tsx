/**
 * @file_name: ChatHeader.tsx
 * @author:
 * @date: 2026-08-06
 * @description: The v4 chat header — the agent's name is the protagonist.
 *
 * Left: sidebar-expand button (only while the sidebar is collapsed), agent
 * ring avatar + agent-name button (navigates to the agent's profile page).
 * Right: the Chat / Inner Thoughts segmented toggle, entry icons for
 * Jobs / Inbox / Artifacts (with live badges from the bookmark registry),
 * the cost popover, and a ⋯ detail
 * menu listing the remaining agent panels (Workspace / Channels / Skills /
 * MCP / Smart Home). Awareness, Network/Memory, and Model & framework were
 * all dropped from this menu (2026-08-25) — they're now reachable from the
 * agent's Profile page, so keeping a second door to the same rooms here
 * was pure duplication. The ⋯ menu is the only entry point for that panel
 * drawer — the avatar/name button is a plain navigation link, not a
 * second way to open it.
 *
 * This component is ENTRY POINTS ONLY: every item opens the same bookmark
 * drawer panels that the retired right-edge BookmarkStrip used to open
 * (via uiStore.requestPanel → ChatView's drawer). Panel internals are
 * unchanged by design — the v4 redesign relocated the doors, not the rooms.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { MoreVertical, ListTodo, Inbox, PanelLeft } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { CostPopover } from '@/components/cost/CostPopover';
import { ExecutionPopover } from './ExecutionPopover';
import {
  STRIP_CATEGORIES,
  ArtifactsGlyph,
  deriveTabStatus,
  markTabOpened,
  tabDescKey,
  type AtomicTabId,
} from '@/components/bookmarks';
import { useUIStore, useArtifactStore } from '@/stores';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { cn } from '@/lib/utils';
import type { Step } from '@/types';

/** Detail-menu layout: config panels only. Awareness and the Network/Memory
 *  pair are dropped here — they now live on the agent's Profile page and
 *  don't need a second door. */
const DETAIL_GROUP_A: AtomicTabId[] = ['workspace', 'channels', 'skills', 'mcp', 'smarthome'];

const ALL_TAB_DEFS = STRIP_CATEGORIES.flatMap((c) => c.tabs);

function tabDef(id: AtomicTabId) {
  return ALL_TAB_DEFS.find((t) => t.id === id)!;
}

export interface ChatHeaderProps {
  agentId: string | null;
  agentName: string;
  isStreaming: boolean;
  currentSteps: Step[];
  chatTab: 'conversation' | 'inner';
  onChatTabChange: (tab: 'conversation' | 'inner') => void;
}

export function ChatHeader({
  agentId,
  agentName,
  isStreaming,
  currentSteps,
  chatTab,
  onChatTabChange,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [detailOpen, setDetailOpen] = useState(false);

  const goToProfile = () => {
    if (!agentId) return;
    navigate(`/app/agents/${encodeURIComponent(agentId)}`, { state: { from: 'chat' } });
  };

  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const requestPanel = useUIStore((s) => s.requestPanel);

  const bookmarkState = useBookmarkStore((s) => (agentId ? s.agents[agentId] : undefined));
  const jobsStatus = deriveTabStatus(bookmarkState, 'jobs');
  const inboxStatus = deriveTabStatus(bookmarkState, 'inbox');

  const artifactCount = useArtifactStore((s) => s.artifacts.length);

  const openPanel = (id: AtomicTabId) => {
    if (!agentId) return;
    setDetailOpen(false);
    markTabOpened(agentId, id);
    requestPanel(id);
  };

  const iconBtn =
    'relative inline-flex h-[30px] w-[30px] items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink50)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]';

  return (
    <div
      className="hidden md:flex items-center justify-between gap-3 px-4 min-h-[52px] shrink-0 border-b"
      style={{ borderColor: 'var(--nm-hairline)' }}
    >
      {/* Left — expand + agent identity */}
      <div className="flex items-center gap-2.5 min-w-0 overflow-hidden">
        {sidebarCollapsed && (
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            title={t('sidebar.expandTitle')}
            aria-label={t('sidebar.expandTitle')}
            className={cn(iconBtn, 'shrink-0 h-7 w-7')}
          >
            <PanelLeft className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          onClick={goToProfile}
          title={t('chat.header.viewProfile')}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-1.5 py-0.5 transition-colors hover:bg-[var(--nm-paper-warm)] shrink-0"
        >
          <RingAvatar
            species="silicon"
            label={(agentName || 'AI').slice(0, 2)}
            size="sm"
            className="shrink-0"
          />
          {/* Same family as the sidebar row that shows this same name — the
              header keeps its lead role via size + weight, not a second
              typeface (design_system.md §4.1: display is for large titles). */}
          <span className="font-[family-name:var(--font-sans)] text-base font-semibold text-[var(--nm-ink)] truncate max-w-[220px]">
            {agentName}
          </span>
        </button>
      </div>

      {/* Right — streaming chip, segmented toggle, entry icons, detail ⋯ */}
      <div className="flex items-center gap-3 shrink-0">
        {isStreaming && <ExecutionPopover steps={currentSteps} />}

        {/* Chat / Inner Thoughts segmented toggle (v4 #11 — the underline
            tab row folded into the header). State lives in ChatPanel. */}
        <div className="inline-flex items-center gap-0.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] p-0.5">
          {(['conversation', 'inner'] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => onChatTabChange(tab)}
              className={cn(
                'rounded-[var(--radius-sm)] px-2.5 py-[3px] text-[11px] transition-colors',
                chatTab === tab
                  ? 'bg-[var(--nm-raised)] font-semibold text-[var(--nm-ink)]'
                  : 'font-medium text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]',
              )}
            >
              {tab === 'conversation' ? t('chat.conversation') : t('chat.innerThoughts')}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => openPanel('jobs')}
            title={t('rail.jobs')}
            data-help-id="bookmarks.jobs"
            className={iconBtn}
          >
            <ListTodo className="h-4 w-4" />
            {jobsStatus.badge ? <HeaderBadge count={jobsStatus.badge} /> : null}
          </button>
          <button
            type="button"
            onClick={() => openPanel('inbox')}
            title={t('rail.inbox')}
            data-help-id="bookmarks.inbox"
            className={iconBtn}
          >
            <Inbox className="h-4 w-4" />
            {inboxStatus.badge ? <HeaderBadge count={inboxStatus.badge} /> : null}
          </button>
          {/* Artifacts — opens the drawer panel like every other entry (the
              resizable side column is retired; Owner 2026-08-06). */}
          <button
            type="button"
            onClick={() => openPanel('artifacts')}
            title={t('rail.artifacts')}
            data-help-id="layout.artifacts"
            className={iconBtn}
          >
            <ArtifactsGlyph className="h-4 w-4" strokeWidth={1.8} />
            {artifactCount > 0 ? <HeaderBadge count={artifactCount} /> : null}
          </button>
          <span data-help-id="chat.cost">
            <CostPopover />
          </span>

          {/* Detail ⋯ menu — every agent panel, one door. */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setDetailOpen((v) => !v)}
              title={t('chat.header.detailMenuTitle')}
              aria-label={t('chat.header.detailMenuTitle')}
              data-help-id="chat.detail-menu"
              className={cn(iconBtn, detailOpen && 'bg-[var(--nm-raised)] text-[var(--nm-ink)]')}
            >
              <MoreVertical className="h-4 w-4" />
            </button>
            {detailOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setDetailOpen(false)} />
                <div
                  className={cn(
                    'absolute right-0 top-full z-50 mt-2 w-[236px] p-1.5',
                    'rounded-[var(--radius-md)] border shadow-[0_8px_24px_rgba(0,0,0,0.14)]',
                    'bg-[var(--nm-card)] border-[var(--nm-hairline)]',
                  )}
                >
                  {DETAIL_GROUP_A.map((id) => (
                    <DetailItem key={id} id={id} onOpen={openPanel} />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function HeaderBadge({ count }: { count: number }) {
  return (
    <span className="absolute right-[3px] top-[2px] flex h-3 min-w-3 items-center justify-center rounded-full bg-[var(--nm-ink)] px-0.5 text-[9px] font-bold leading-none text-[var(--nm-paper)]">
      {count > 99 ? '99+' : count}
    </span>
  );
}

function DetailItem({ id, onOpen }: { id: AtomicTabId; onOpen: (id: AtomicTabId) => void }) {
  const { t } = useTranslation();
  const def = tabDef(id);
  const Icon = def.icon;
  return (
    <button
      type="button"
      onClick={() => onOpen(id)}
      // Hover title = the same one-sentence explainer the drawer header's
      // ? icon shows — orientation before the click, not only after.
      title={t(tabDescKey(id), '')}
      className="w-full flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-[7px] text-left text-[13px] font-medium text-[var(--nm-ink)] transition-colors hover:bg-[var(--nm-paper-warm)]"
    >
      <Icon className="h-[15px] w-[15px] text-[var(--nm-ink70)]" />
      {t(def.labelKey, def.label)}
    </button>
  );
}
