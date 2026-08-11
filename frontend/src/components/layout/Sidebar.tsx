/**
 * Sidebar — Chat UI v4 three-zone shell.
 *
 * Zone 1: logo + collapse (panel icon). Zone 2: global nav (New / Export /
 * Dashboard / Marketplace / Settings) then the Chats list (teams + agents,
 * owned by AgentList — the sidebar stays "a shell, not a list owner").
 * Zone 3: the user row opening an account popover (workspace / account /
 * billing / subscription / theme / language / logout) + the Find Us entry.
 *
 * Collapse hides the whole aside (uiStore.sidebarCollapsed); the expand
 * button renders in the chat header / a floating chip, outside this tree.
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useIsMobile } from '@/hooks/useMediaQuery';
import {
  LogOut,
  PanelLeft,
  Sliders,
  Server,
  Monitor,
  Cloud,
  RotateCcw,
  LayoutDashboard,
  Store,
  Upload,
  User,
  CreditCard,
  Star,
  Sun,
  Moon,
  Globe,
  Users,
  BookOpen,
  Check,
  ChevronsUpDown,
} from 'lucide-react';
import { BetaBadge, useConfirm } from '@/components/ui';
import { FeedbackDialog } from '@/components/ui/FeedbackDialog';
import { RingAvatar, StatusDot } from '@/components/nm';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/hooks';
import { useCreateAgent, useAgentImported } from '@/hooks';
import { SUPPORTED_LANGUAGES } from '@/i18n';
import {
  useConfigStore,
  useChatStore,
  useRuntimeStore,
  usePreloadStore,
  useUIStore,
} from '@/stores';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui';
import { AgentList } from './AgentList';
import { CreateMenu } from './CreateMenu';
import { ImportAgentModal } from './ImportAgentModal';
import { FIND_US_URL } from './TopBar';

// v2.2 G1: prefetch the lazy DashboardPage chunk on hover/focus so click
// arrives to a warm cache. Static literal -> Vite resolves at build time,
// no injection risk.
const prefetchDashboard = () => {
  void import('@/pages/DashboardPage');
};

// v4 nav row treatment: flat rows, raised bg on hover/active (design mock),
// ink70 label brightening to ink.
const NAV_ROW =
  'w-full flex items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left transition-colors text-[var(--nm-ink70)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]';
const NAV_ROW_ACTIVE = 'bg-[var(--nm-raised)] text-[var(--nm-ink)]';

export function Sidebar() {
  const [showModePopup, setShowModePopup] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  // Mobile-only feedback entry. Desktop uses the floating FeedbackButton
  // (bottom-right, by the help "?"); on mobile that corner is the composer's,
  // so the drawer footer carries it instead. Exactly one entry per viewport.
  const [showFeedback, setShowFeedback] = useState(false);
  // Mobile (< md): the sidebar is an off-canvas drawer toggled from the
  // mobile top strip. Desktop (md+): collapse hides the aside entirely.
  const mobileNavOpen = useUIStore((s) => s.mobileNavOpen);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const location = useLocation();

  const { userId, displayName, logout } = useConfigStore();
  const netmindToken = useConfigStore((s) => s.netmindToken);
  // user_id is an opaque NetMind userSystemCode (32-hex) in cloud mode, not
  // human-readable. Show the NetMind nickname when we have it; fall back to
  // user_id (local mode, where it IS the chosen username).
  const userLabel = displayName || userId;
  const { clearAll: clearChat } = useChatStore();
  const { mode, features, setMode, setCloudApiUrl } = useRuntimeStore();
  const clearPreload = usePreloadStore((s) => s.clearAll);
  const { createAgent, creating: creatingAgent } = useCreateAgent();
  const handleImportApplied = useAgentImported();
  // Import-from-other-source is local-only: the scanner reads the user's
  // filesystem, and detect/scan 503 on cloud (see backend/routes/migrate.py).
  const isLocalMode = mode === 'local';

  // The cloud/local mode switcher is hidden — we don't want users choosing
  // the deployment mode. All the switching logic (handleSwitchMode, mode
  // state) is kept intact behind this flag so it can be re-enabled by
  // flipping to true; only the UI entry points are gated.
  const SHOW_MODE_SWITCHER = false;
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { theme, toggleTheme, isDark } = useTheme();
  const { t, i18n } = useTranslation();

  const currentLang =
    SUPPORTED_LANGUAGES.find(
      (l) => i18n.resolvedLanguage === l.code || i18n.language?.startsWith(l.code),
    ) ?? SUPPORTED_LANGUAGES[0];
  const themeLabel =
    theme === 'system'
      ? t('sidebar.themeSystem')
      : isDark
        ? t('sidebar.themeDark')
        : t('sidebar.themeLight');
  const modeLabel = mode === 'local' ? t('sidebar.local') : t('sidebar.cloud');

  /**
   * Wipe all session + cached data before leaving the current mode.
   *
   * This is deliberately aggressive. We do NOT trust Zustand's persist
   * middleware to have flushed to localStorage by the time the subsequent
   * window.location.href reload happens — so we also manually
   * `removeItem()` every known persisted key. After the reload each store
   * will re-hydrate from whatever is (or is not) in localStorage, so
   * removed keys mean default-state stores.
   *
   * Keys wiped:
   *   - narra-nexus-config  → configStore (userId, token, agents, ...)
   *   - narranexus-runtime  → runtimeStore (mode, cloudApiUrl, ...)
   *   - lastSeenAwarenessTime:*  → written directly by configStore, not
   *                                 covered by any store's clearAll
   */
  const wipeAllSessionData = () => {
    // 1. Reset in-memory store state via each store's clearAll/logout.
    //    This updates the UI immediately and invokes persist middleware
    //    to sync localStorage (best-effort — we do not rely on it).
    logout();           // configStore
    clearChat();        // chatStore
    clearPreload();     // preloadStore

    // 2. Directly nuke every key in localStorage that could carry
    //    session state. This is the authoritative clear, independent
    //    of whatever Zustand persist may or may not have flushed yet.
    try {
      localStorage.removeItem('narra-nexus-config');
      localStorage.removeItem('narranexus-runtime');

      const auxKeys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('lastSeenAwarenessTime:')) {
          auxKeys.push(k);
        }
      }
      auxKeys.forEach((k) => localStorage.removeItem(k));
    } catch {
      // Safari private mode / other storage exceptions — ignore.
    }
  };

  const handleSwitchMode = () => {
    wipeAllSessionData();
    setCloudApiUrl('');
    setMode(null);
    setShowModePopup(false);

    // Hard reload, NOT React Router navigate. Soft navigation keeps the
    // React tree, closure-captured store snapshots, in-flight fetches,
    // and module-level caches from the previous mode alive — which is
    // exactly how cloud data was bleeding into a subsequent local
    // session. A full document reload tears everything down.
    //
    // Combined with the localStorage.removeItem() calls above, the next
    // page load starts from true factory defaults.
    window.location.href = '/mode-select';
  };

  const handleLogout = async () => {
    setAccountOpen(false);
    const ok = await confirm({
      title: t('layout.sidebar.logoutConfirmTitle'),
      message: t('layout.sidebar.logoutConfirmMessage'),
      confirmText: t('layout.sidebar.logoutConfirmAction'),
      danger: true,
    });
    if (!ok) return;
    wipeAllSessionData();
    window.location.href = '/login';
  };

  const accountNavigate = (to: string) => {
    setAccountOpen(false);
    setLangOpen(false);
    navigate(to);
  };

  return (
    <aside
      className={cn(
        'flex flex-col relative',
        // NM canonical (FinChats:461): chat-list container bg = var(--nm-paper).
        // Rows sit on paper directly with rounded highlight when active.
        'bg-[color:var(--nm-paper)]',
        'border-r border-[color:var(--nm-hairline)]',
        // Mobile (< md): off-canvas drawer below the 36px mobile top strip.
        // Height comes from top-9 + bottom-0 (NOT h-full, which would overflow
        // 36px below the viewport and clip the footer).
        'fixed top-9 bottom-0 left-0 z-40 w-[272px] transition-transform duration-300 ease-out',
        mobileNavOpen ? 'translate-x-0 shadow-[var(--nm-elev-3)]' : '-translate-x-full',
        // Tablet/desktop (md+): back in normal flow, full height; v4 collapse
        // hides the whole aside (the expand affordance lives outside it).
        'md:static md:top-auto md:bottom-auto md:z-auto md:h-full md:translate-x-0 md:shadow-none',
        sidebarCollapsed ? 'md:hidden' : 'md:flex',
      )}
    >
      {confirmDialog}
      {isMobile && <FeedbackDialog isOpen={showFeedback} onClose={() => setShowFeedback(false)} />}
      {importOpen && (
        <ImportAgentModal
          onClose={() => setImportOpen(false)}
          onApplied={handleImportApplied}
        />
      )}

      {/* ── Zone 1: logo + collapse ─────────────────────────────────────── */}
      <div className="px-4 pt-3.5 pb-2.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <img
            src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
            alt="NarraNexus"
            className="h-9 w-auto object-contain shrink-0"
          />
          <BetaBadge className="shrink-0" />
        </div>
        <button
          type="button"
          onClick={toggleSidebar}
          title={t('sidebar.collapseTitle')}
          aria-label={t('sidebar.collapseTitle')}
          className="shrink-0 hidden md:inline-flex items-center justify-center w-7 h-7 rounded-[var(--radius-sm)] text-[var(--nm-ink50)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)] transition-colors"
        >
          <PanelLeft className="w-4 h-4" />
        </button>
      </div>

      {/* ── Zone 2a: global nav ─────────────────────────────────────────── */}
      <div className="px-2 pb-2 flex flex-col gap-px border-b border-[var(--nm-hairline)]">
        <span data-help-id="sidebar.create-agent">
          <CreateMenu
            onCreateAgent={() => void createAgent()}
            onCreateTeam={() => navigate('/app/teams/new')}
            onImportBundle={() => navigate('/app/bundle/import')}
            onImportAgent={isLocalMode ? () => setImportOpen(true) : undefined}
            disabled={creatingAgent}
          />
        </span>
        <button
          type="button"
          onClick={() => navigate('/app/bundle/export')}
          title={t('sidebar.exportTitle')}
          data-help-id="sidebar.export"
          className={cn(NAV_ROW, location.pathname === '/app/bundle/export' && NAV_ROW_ACTIVE)}
        >
          <Upload className="w-4 h-4 shrink-0" />
          {t('sidebar.export')}
        </button>
        <button
          type="button"
          onClick={() => navigate('/app/dashboard')}
          onMouseEnter={prefetchDashboard}
          onFocus={prefetchDashboard}
          data-help-id="sidebar.manage-agents"
          className={cn(NAV_ROW, location.pathname === '/app/dashboard' && NAV_ROW_ACTIVE)}
        >
          <LayoutDashboard className="w-4 h-4 shrink-0" />
          {t('sidebar.dashboard')}
        </button>
        <button
          type="button"
          onClick={() => navigate('/app/marketplace')}
          className={cn(NAV_ROW, location.pathname === '/app/marketplace' && NAV_ROW_ACTIVE)}
        >
          <Store className="w-4 h-4 shrink-0" />
          {t('sidebar.marketplace')}
        </button>
        <button
          type="button"
          onClick={() => navigate('/app/settings')}
          title={t('sidebar.settingsTitle')}
          className={cn(NAV_ROW, location.pathname === '/app/settings' && NAV_ROW_ACTIVE)}
        >
          <Sliders className="w-4 h-4 shrink-0" />
          {t('sidebar.settings')}
        </button>
        {features.showSystemPage && (
          <button
            type="button"
            onClick={() => navigate('/app/system')}
            className={cn(NAV_ROW, location.pathname === '/app/system' && NAV_ROW_ACTIVE)}
          >
            <Server className="w-4 h-4 shrink-0" />
            {t('sidebar.system')}
          </button>
        )}
      </div>

      {/* ── Zone 2b: Chats (teams + agents, owned by AgentList) ─────────── */}
      <ScrollArea className="flex-1">
        <AgentList />
      </ScrollArea>

      {/* ── Zone 3: user row + account popover + Find Us ────────────────── */}
      <div className="p-2 border-t border-[var(--nm-hairline)] relative">
        {accountOpen && (
          <>
            <div
              className="fixed inset-0 z-40"
              onClick={() => { setAccountOpen(false); setLangOpen(false); }}
            />
            <div
              className={cn(
                'absolute bottom-full left-2 right-2 mb-1.5 z-50 p-1.5',
                'rounded-[var(--radius-md)] border shadow-[0_8px_24px_rgba(0,0,0,0.14)]',
                'bg-[var(--nm-card)] border-[var(--nm-hairline)]',
              )}
            >
              <div className="px-2.5 pt-2 pb-1.5 border-b border-[var(--nm-hairline)] mb-1">
                <div className="text-[13px] font-semibold text-[var(--nm-ink)] truncate" title={userLabel}>
                  {userLabel}
                </div>
                <div className="text-[10px] text-[var(--nm-ink50)]">
                  {t('sidebar.online')} · {modeLabel} · v{__APP_VERSION__}
                </div>
              </div>
              <AccountItem
                icon={<BookOpen className="w-3.5 h-3.5" />}
                label={t('sidebar.workspace')}
                onClick={() => accountNavigate('/app/you')}
              />
              {/* Account / billing / subscription — the user-scoped
                  /app/account page (moved out of app Settings); present only
                  for NetMind-authenticated (power) users. */}
              {netmindToken && (
                <>
                  <AccountItem
                    icon={<User className="w-3.5 h-3.5" />}
                    label={t('sidebar.account')}
                    onClick={() => accountNavigate('/app/account')}
                  />
                  <AccountItem
                    icon={<CreditCard className="w-3.5 h-3.5" />}
                    label={t('sidebar.billing')}
                    onClick={() => accountNavigate('/app/account')}
                  />
                  <AccountItem
                    icon={<Star className="w-3.5 h-3.5" />}
                    label={t('sidebar.subscription')}
                    onClick={() => accountNavigate('/app/account')}
                  />
                </>
              )}
              <div className="my-1 mx-1 border-t border-[var(--nm-hairline)]" />
              <AccountItem
                icon={theme === 'system' ? <Monitor className="w-3.5 h-3.5" /> : isDark ? <Moon className="w-3.5 h-3.5" /> : <Sun className="w-3.5 h-3.5" />}
                label={t('sidebar.theme')}
                hint={themeLabel}
                onClick={toggleTheme}
              />
              <AccountItem
                icon={<Globe className="w-3.5 h-3.5" />}
                label={t('sidebar.language')}
                hint={currentLang.code.toUpperCase()}
                onClick={() => setLangOpen((v) => !v)}
              />
              {langOpen && (
                <div className="max-h-56 overflow-y-auto mx-1 mb-1 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)]">
                  {SUPPORTED_LANGUAGES.map((l) => {
                    const active = l.code === currentLang.code;
                    return (
                      <button
                        key={l.code}
                        type="button"
                        dir={l.code === 'ar' ? 'rtl' : undefined}
                        onClick={() => { void i18n.changeLanguage(l.code); setLangOpen(false); }}
                        className={cn(
                          'w-full flex items-center gap-2 px-2.5 py-1.5 text-[12px] text-left transition-colors',
                          'hover:bg-[var(--nm-paper-warm)]',
                          active ? 'text-[var(--nm-ink)] font-medium' : 'text-[var(--nm-ink70)]',
                        )}
                      >
                        <span className="text-sm leading-none">{l.flag}</span>
                        <span className="flex-1">{l.label}</span>
                        {active && <Check className="w-3 h-3 shrink-0" />}
                      </button>
                    );
                  })}
                </div>
              )}
              {isMobile && (
                <AccountItem
                  icon={<Users className="w-3.5 h-3.5" />}
                  label={t('feedback.title')}
                  onClick={() => { setAccountOpen(false); setShowFeedback(true); }}
                />
              )}
              {SHOW_MODE_SWITCHER && (
                <AccountItem
                  icon={mode === 'local' ? <Monitor className="w-3.5 h-3.5" /> : <Cloud className="w-3.5 h-3.5" />}
                  label={t('layout.sidebar.switchTo', {
                    mode: mode === 'local' ? t('sidebar.cloud') : t('sidebar.local'),
                  })}
                  onClick={() => setShowModePopup(!showModePopup)}
                />
              )}
              {SHOW_MODE_SWITCHER && showModePopup && (
                <button
                  type="button"
                  onClick={handleSwitchMode}
                  className="w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left text-[var(--nm-ink70)] hover:bg-[var(--nm-paper-warm)]"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  {t('layout.sidebar.currentMode', {
                    mode: mode === 'local' ? t('sidebar.localMode') : t('sidebar.cloudMode'),
                  })}
                </button>
              )}
              <div className="my-1 mx-1 border-t border-[var(--nm-hairline)]" />
              <AccountItem
                icon={<LogOut className="w-3.5 h-3.5" />}
                label={t('sidebar.logout')}
                danger
                onClick={handleLogout}
              />
              <div className="px-2.5 pt-1 pb-0.5 text-[9px] text-[var(--nm-ink30)] font-mono tracking-wider truncate">
                {t('sidebar.poweredBy')}
              </div>
            </div>
          </>
        )}

        <div className="flex items-stretch gap-1.5">
          <button
            type="button"
            onClick={() => setAccountOpen((v) => !v)}
            title={t('sidebar.accountTitle')}
            aria-label={t('sidebar.accountTitle')}
            className={cn(
              'flex flex-1 min-w-0 items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] text-left transition-colors',
              accountOpen ? 'bg-[var(--nm-raised)]' : 'hover:bg-[var(--nm-paper-warm)]',
            )}
          >
            <RingAvatar species="carbon" label={userLabel || '?'} size="sm" />
            <span className="flex-1 min-w-0 flex flex-col justify-center gap-0.5">
              <span className="text-[13px] font-semibold leading-tight text-[var(--nm-ink)] truncate" title={userLabel}>
                {userLabel}
              </span>
              <span className="flex items-center gap-1.5 text-[10px] leading-tight text-[var(--nm-ink50)]">
                <StatusDot status="success" size={6} />
                {t('sidebar.online')} · {modeLabel}
              </span>
            </span>
            <ChevronsUpDown className="w-3.5 h-3.5 shrink-0 text-[var(--nm-ink30)]" aria-hidden />
          </button>
          {/* Community entry — must stay a plain external link (no in-app
              routing); shortens the "sign up → join the community" path. */}
          <a
            href={FIND_US_URL}
            target="_blank"
            rel="noopener noreferrer"
            title={t('layout.topBar.findUsTitle')}
            data-help-id="sidebar.find-us"
            className="shrink-0 inline-flex flex-col items-center justify-center gap-1 px-2.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] text-[var(--nm-ink50)] no-underline text-[9px] font-medium transition-colors hover:text-[var(--nm-ink)] hover:border-[var(--border-strong)]"
          >
            <Users className="w-3.5 h-3.5" />
            {t('layout.topBar.findUs')}
          </a>
        </div>
      </div>
    </aside>
  );
}

function AccountItem({
  icon,
  label,
  hint,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left transition-colors',
        danger
          ? 'text-[var(--color-error)] hover:bg-[rgba(201,90,77,0.08)]'
          : 'text-[var(--nm-ink70)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]',
      )}
    >
      {icon}
      <span className="flex-1 min-w-0 truncate">{label}</span>
      {hint && (
        <span className="text-[10px] font-mono text-[var(--nm-ink30)] shrink-0">{hint}</span>
      )}
    </button>
  );
}
