/**
 * @file_name: builtin.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The shell registers its own pages, sidebar rows, panels and settings sections here.
 *
 * "Builtins are plugins too": the shell contributes through the same
 * registries a plugin uses, in one place, in the order the UI shows them.
 * Imported once from `main.tsx` before the first render; page and panel
 * components stay lazy so this file adds only metadata to the first chunk.
 */
import { createElement, lazy } from 'react';
import { Navigate } from 'react-router-dom';
import { BookOpen, LayoutDashboard, Server, Sliders, Store, Upload } from 'lucide-react';

import { CONVERSATION_KINDS, PAGES, PANELS, SIDEBAR } from '@/platform/registries';
import {
  ArtifactsTab,
  AwarenessTab,
  BuilderTab,
  ChannelsTab,
  InboxTab,
  JobsTab,
  McpTab,
  MemoryTab,
  SkillsTab,
  SmartHomeTab,
  SocialTab,
  WorkspaceTab,
} from '@/platform/builtinPanels';

const OWNER = { owner: 'builtin.ui' };

function SetupRedirect() {
  return createElement(Navigate, { to: '/welcome', replace: true });
}
// Feature-level builtin plugin: its UI row disappears when builtin.teams is disabled (loader.disableBuiltinUi).
const TEAMS = { owner: 'builtin.teams' };

// ---------------------------------------------------------------- pages
// Top-level routes (outside the /app layout).
PAGES.register('login', { path: '/login', element: lazy(() => import('@/pages/LoginPage')), guard: 'public', layout: 'top' }, OWNER);
// NM design system gallery — public dev tool, no auth required.
PAGES.register('nm-playground', { path: '/nm-playground', element: lazy(() => import('@/pages/NMPlaygroundPage')), guard: 'open', layout: 'top' }, OWNER);
// First-run flow (dev #383): /welcome gates itself; /setup was the provider-only
// first-run page and now forwards there (docs and old bookmarks point at it).
PAGES.register('welcome', { path: '/welcome', element: lazy(() => import('@/pages/WelcomePage')), guard: 'protected', layout: 'top', skipWelcomeGate: true }, OWNER);
PAGES.register('setup', { path: '/setup', element: SetupRedirect, guard: 'open', layout: 'top' }, OWNER);
// Website-to-Stripe bounce: the pricing page's plan CTAs point here. The
// protected guard gives a logged-out visitor /login?next=%2Fpay so the payment
// intent survives login/signup; PayPage then mints the checkout session.
// Carries a payment intent through login and out to Stripe: never interrupted by the welcome gate.
PAGES.register('pay', { path: '/pay', element: lazy(() => import('@/pages/PayPage')), guard: 'protected', layout: 'top', skipWelcomeGate: true }, OWNER);

// Children of the /app layout (MainLayout). Order is route order.
// `chat` and `teams/:teamId/chat` render nothing here: MainLayout draws the
// chat views in its main slot so they are not sub-page overlays.
PAGES.register('chat', { path: 'chat', element: null, guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('dashboard', { path: 'dashboard', element: lazy(() => import('@/pages/DashboardPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('marketplace', { path: 'marketplace', element: lazy(() => import('@/pages/MarketplacePage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('you', { path: 'you', element: lazy(() => import('@/pages/YouWorkspace')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('system', { path: 'system', element: lazy(() => import('@/pages/SystemPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('settings', { path: 'settings', element: lazy(() => import('@/pages/SettingsPage')), guard: 'protected', layout: 'app' }, OWNER);
// Legacy alias: the account surface lives inside Settings (?tab=account);
// this route only forwards old links there with the query preserved.
PAGES.register('account', { path: 'account', element: lazy(() => import('@/pages/AccountPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('bundle-export', { path: 'bundle/export', element: lazy(() => import('@/pages/BundleExportPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('bundle-import', { path: 'bundle/import', element: lazy(() => import('@/pages/BundleImportPage')), guard: 'protected', layout: 'app' }, OWNER);
// Deep-link entry from the narra.nexus templates marketplace: same component
// as bundle/import; the URL query (?url=&sha256=) triggers auto-fetch-then-preflight.
PAGES.register('templates-install', { path: 'templates/install', element: lazy(() => import('@/pages/BundleImportPage')), guard: 'protected', layout: 'app' }, OWNER);
// Static segment ranks above :teamId in v6 route ranking, but it also reads clearer listed first.
// Creation studio entry + the agent profile page (dev #382/#383). `agents/new` is a
// static segment and must be registered before `agents/:agentId`.
PAGES.register('agents-new', { path: 'agents/new', element: lazy(() => import('@/pages/ChooseCreateMethodPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('agent-profile', { path: 'agents/:agentId', element: lazy(() => import('@/pages/AgentProfilePage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('teams-new', { path: 'teams/new', element: lazy(() => import('@/pages/CreateTeamPage')), guard: 'protected', layout: 'app' }, TEAMS);
PAGES.register('team-detail', { path: 'teams/:teamId', element: lazy(() => import('@/pages/TeamDetailPage')), guard: 'protected', layout: 'app' }, TEAMS);
PAGES.register('team-chat', { path: 'teams/:teamId/chat', element: null, guard: 'protected', layout: 'app' }, TEAMS);

// -------------------------------------------------------------- conversation kinds
// `when: conversationKind:<k>` may name these; the shell's single chat is
// `chat`, builtin.teams brings `team` (gone with the plugin).
CONVERSATION_KINDS.register('chat', { labelKey: 'sidebar.chats' }, OWNER);
CONVERSATION_KINDS.register('team', { labelKey: 'sidebar.teams' }, TEAMS);

// -------------------------------------------------------------- sidebar
const prefetchDashboard = () => {
  // The dashboard chunk is the largest sub-page; warm it on hover/focus.
  import('@/pages/DashboardPage').catch(() => {});
};
const dashboardTab = (search: string) => new URLSearchParams(search).get('tab');

SIDEBAR.register('export', {
  labelKey: 'sidebar.export',
  titleKey: 'sidebar.exportTitle',
  icon: Upload,
  to: '/app/dashboard?tab=export',
  order: 10,
  helpId: 'sidebar.export',
  prefetch: prefetchDashboard,
  isActive: (loc) => loc.pathname === '/app/dashboard' && dashboardTab(loc.search) === 'export',
}, OWNER);
SIDEBAR.register('dashboard', {
  labelKey: 'sidebar.dashboard',
  icon: LayoutDashboard,
  to: '/app/dashboard',
  order: 20,
  helpId: 'sidebar.manage-agents',
  prefetch: prefetchDashboard,
  isActive: (loc) => loc.pathname === '/app/dashboard' && dashboardTab(loc.search) !== 'export',
}, OWNER);
SIDEBAR.register('marketplace', { labelKey: 'sidebar.marketplace', icon: Store, to: '/app/marketplace', order: 30 }, OWNER);
SIDEBAR.register('workspace', { labelKey: 'sidebar.workspace', icon: BookOpen, to: '/app/you', order: 40 }, OWNER);
SIDEBAR.register('settings', { labelKey: 'sidebar.settings', titleKey: 'sidebar.settingsTitle', icon: Sliders, to: '/app/settings', order: 50 }, OWNER);
SIDEBAR.register('system', {
  labelKey: 'sidebar.system',
  icon: Server,
  to: '/app/system',
  order: 60,
  visible: (features) => features.showSystemPage,
}, OWNER);

// --------------------------------------------------------------- panels
// Creation studio panel (dev #382): conditional — renders only while the studio is open on this agent.
PANELS.register('builder', { component: BuilderTab }, OWNER);
PANELS.register('awareness', { component: AwarenessTab }, OWNER);
PANELS.register('workspace', { component: WorkspaceTab }, OWNER);
PANELS.register('channels', { component: ChannelsTab }, OWNER);
PANELS.register('smarthome', { component: SmartHomeTab }, OWNER);
PANELS.register('social', { component: SocialTab }, OWNER);
PANELS.register('jobs', { component: JobsTab }, OWNER);
PANELS.register('inbox', { component: InboxTab }, OWNER);
PANELS.register('artifacts', { component: ArtifactsTab }, OWNER);
PANELS.register('skills', { component: SkillsTab }, OWNER);
PANELS.register('mcp', { component: McpTab }, OWNER);
PANELS.register('memory', { component: MemoryTab }, OWNER);

// Settings sections are registered by `pages/settings/registerBuiltinSections.ts`
// from inside the settings chunk (the panes stay lazy with the page).
