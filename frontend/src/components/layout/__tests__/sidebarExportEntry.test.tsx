/**
 * @file_name: sidebarExportEntry.test.tsx
 * @description: Req #1 (导出融入智能体管理) — the sidebar "Export" nav row now
 * deep-links into the Dashboard's Export tab, not the standalone bundle page.
 * Reverting the target turns this red.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate, useLocation: () => ({ pathname: '/app/chat', search: '' }) };
});

const cfg = { userId: 'u1', displayName: 'U', logout: vi.fn(), netmindToken: null };
const uiState = { mobileNavOpen: false, sidebarCollapsed: false, toggleSidebar: vi.fn() };
vi.mock('@/stores', () => ({
  useConfigStore: (sel?: (s: unknown) => unknown) => (sel ? sel(cfg) : cfg),
  useChatStore: () => ({ clearAll: vi.fn() }),
  useRuntimeStore: () => ({ mode: 'local', features: { showSystemPage: false }, setMode: vi.fn(), setCloudApiUrl: vi.fn() }),
  usePreloadStore: (sel: (s: unknown) => unknown) => sel({ clearAll: vi.fn() }),
  useUIStore: (sel: (s: unknown) => unknown) => sel(uiState),
}));
vi.mock('@/hooks', () => ({
  useTheme: () => ({ isDark: false }),
  useCreateAgent: () => ({ createAgent: vi.fn(), creating: false }),
  useAgentImported: () => vi.fn(),
  useDismissOnOutside: () => ({ current: null }),
}));
vi.mock('@/hooks/useMediaQuery', () => ({ useIsMobile: () => false }));
vi.mock('@/components/ui', () => ({
  BetaBadge: () => null,
  ScrollArea: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  useConfirm: () => ({ confirm: vi.fn(), dialog: null }),
}));
vi.mock('@/components/ui/FeedbackDialog', () => ({ FeedbackDialog: () => null }));
vi.mock('@/components/nm', () => ({ RingAvatar: () => null, StatusDot: () => null }));
vi.mock('../AgentList', () => ({ AgentList: () => null }));
vi.mock('../CreateMenu', () => ({ CreateMenu: () => null }));
vi.mock('../ImportAgentModal', () => ({ ImportAgentModal: () => null }));

import { Sidebar } from '../Sidebar';

describe('sidebar Export row (#1 融入)', () => {
  it('deep-links into the dashboard export tab', () => {
    render(<Sidebar />);
    fireEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(navigate).toHaveBeenCalledWith('/app/dashboard?tab=export');
  });
});
