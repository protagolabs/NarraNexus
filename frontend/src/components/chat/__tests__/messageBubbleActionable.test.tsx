/**
 * @file_name: messageBubbleActionable.test.tsx
 * @date: 2026-07-14
 * @description: A config_actionable (deterministic self-serviceable) failure
 * must render "what you can do" guidance, not a generic "Run failed".
 *
 * The "black box" P1: a 32k model that can't hold the platform context failed
 * every turn and the fallback masked it. Now such a turn surfaces isError +
 * actionReason; the popover shows the localized actionable title + per-reason
 * guidance so the user knows to switch models. This guards that wiring.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Assert WHERE the free-tier buttons send the user, not just that they exist:
// the whole point of this funnel is landing on the right Settings panel.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}));

import { MessageBubble } from '../MessageBubble';
import type { ChatMessage } from '@/types';

function msg(p: Partial<ChatMessage>): ChatMessage {
  return { id: 'm1', role: 'assistant', content: 'ctx too small', timestamp: 0, ...p };
}

// MessageBubble reads `useNavigate` (the free-tier remedy buttons deep-link into
// Settings), so it needs a router in tests — as it always has at runtime, where
// chat lives under /app. Rendering it bare only worked while it happened to use
// no router hooks.
const renderBubble = (ui: React.ReactElement) =>
  render(<MemoryRouter>{ui}</MemoryRouter>);

describe('MessageBubble actionable error', () => {
  it('shows the badge for a config_actionable failure', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    expect(screen.getByLabelText('Show error details')).toBeInTheDocument();
  });

  it('shows the localized guidance in the bubble body (not the raw error blob)', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window', content: 'raw english + json blob' })} />);
    // Body renders the localized guidance, NOT the raw content blob.
    expect(screen.getByText(/larger context window/i)).toBeInTheDocument();
    expect(screen.queryByText('raw english + json blob')).not.toBeInTheDocument();
  });

  it('renders actionable popover instead of generic "Run failed"', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'context_window' })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    // Actionable title (popover only) + guidance now appears in BOTH body and
    // popover, so allow multiple matches.
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getAllByText(/larger context window/i).length).toBeGreaterThanOrEqual(1);
    // NOT the generic failure copy.
    expect(screen.queryByText('Run failed')).not.toBeInTheDocument();
  });

  it('falls back to generic failure copy when no actionReason', () => {
    renderBubble(<MessageBubble message={msg({ isError: true })} />);
    fireEvent.click(screen.getByLabelText('Show error details'));
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.queryByText('Action needed')).not.toBeInTheDocument();
  });
});

// ── free-tier exhaustion: the restored monetisation funnel (2026-07-30) ──────
// Before the free tier became an ordinary provider card this guidance lived in a
// global HTTP-402 banner. The 402 went away with the pre-run quota gate and took
// the funnel with it, leaving exhausted users a message whose two suggestions —
// top up, re-paste the key — are both impossible for this card. These pin the
// replacement: the paths that DO exist, offered inline.
describe('MessageBubble free-tier exhaustion', () => {
  beforeEach(() => mockNavigate.mockReset());

  it('offers both available paths', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    expect(screen.getByRole('button', { name: /Get Nexus Pro/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use my own provider/i })).toBeInTheDocument();
  });

  it('never suggests a top-up or re-pasting the key — neither is possible here', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    expect(screen.queryByText(/top up/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/re-paste/i)).not.toBeInTheDocument();
  });

  it('sends the upgrade button to /pay, not the settings detour', () => {
    // /pay mints the checkout session and redirects to Stripe in one hop (#223).
    // Its degenerate cases — already subscribed, desktop webview, non-Power,
    // 401 — all fall back to the account page this button used to target, so
    // routing through settings buys nothing.
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Get Nexus Pro/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/pay');
  });

  it('sends "Use my own provider" to the providers panel', () => {
    renderBubble(<MessageBubble message={msg({ isError: true, actionReason: 'free_tier_exhausted' })} />);
    fireEvent.click(screen.getByRole('button', { name: /Use my own provider/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/app/settings?tab=providers');
  });

  it('shows NO buttons for any other actionable reason', () => {
    // A BYOK wallet running dry keeps the generic guidance: a top-up IS possible
    // on the user's own account, so hijacking it with an upsell would be wrong.
    for (const reason of ['insufficient_balance', 'context_window', 'model_not_found']) {
      const { unmount } = renderBubble(
        <MessageBubble message={msg({ isError: true, actionReason: reason })} />,
      );
      expect(screen.queryByRole('button', { name: /Get Nexus Pro/i })).not.toBeInTheDocument();
      unmount();
    }
  });
});
