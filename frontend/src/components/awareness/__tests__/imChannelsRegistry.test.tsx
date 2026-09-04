/**
 * @file_name: imChannelsRegistry.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The Channels section lists rows from the ui.channels registry: the six builtins (owned by their plugins), a plugin's channel, and none for a disabled builtin.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageSquare } from 'lucide-react';

import { CHANNELS } from '@/platform/registries';
import { IMChannelsSection } from '../IMChannelsSection';

const unbound = vi.fn(async () => ({ success: false }));
vi.mock('@/lib/api', () => ({
  api: {
    channelCredential: (...a: unknown[]) => unbound(...a),
  },
}));
vi.mock('@/stores', () => ({ useConfigStore: () => ({ agentId: 'a1' }) }));

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('channels registry', () => {
  it('the six builtin rows are owned by their channel plugins', () => {
    render(<IMChannelsSection />);
    const owners = Object.fromEntries(CHANNELS.list().map((e) => [e.id, e.owner]));
    expect(owners).toMatchObject({ lark: 'builtin.channels.lark', discord: 'builtin.channels.discord', wechat: 'builtin.channels.wechat' });
    expect(screen.getByText('Lark / Feishu')).toBeInTheDocument();
    expect(screen.getByText('Discord')).toBeInTheDocument();
  });

  it('a plugin channel appears in order and a disabled builtin disappears', async () => {
    disposers.push(CHANNELS.register('acme_chat', { label: 'Acme Chat', icon: MessageSquare, order: 25, component: () => <p>acme config</p>, fetchStatus: async () => 'active' }, { owner: 'acme.chat' }));
    const larkDef = CHANNELS.get('lark')!;
    CHANNELS.removeOwner('builtin.channels.lark');
    disposers.push(() => CHANNELS.register('lark', larkDef, { owner: 'builtin.channels.lark' }));
    render(<IMChannelsSection />);
    expect(screen.queryByText('Lark / Feishu')).toBeNull();
    const labels = ['Slack', 'Acme Chat', 'Telegram'].map((l) => screen.getByText(l));
    expect(labels[0].compareDocumentPosition(labels[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(labels[1].compareDocumentPosition(labels[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => expect(unbound).toHaveBeenCalled());
  });
});
