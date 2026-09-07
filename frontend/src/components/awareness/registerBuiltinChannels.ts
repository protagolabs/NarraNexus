/**
 * @file_name: registerBuiltinChannels.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The six builtin IM channels' rows in the `ui.channels` registry, each owned by its channel plugin.
 *
 * Imported by `IMChannelsSection` (the registration side effect runs once
 * per module graph, but the six lines below are still each independently
 * idempotent — see the "guarded per row" note).
 * Owners are the builtin plugin ids so `disableBuiltinUi('builtin.channels.<x>')`
 * (called at boot, before this lazily-imported chunk ever loads) blacklists the
 * owner: `Registry.register` silently drops that one row's registration below
 * even though it runs after the disable call, and `IMChannelsSection` never
 * lists it.
 */
import { Bot, Hash, MessageCircle, MessageSquare, QrCode, Send } from 'lucide-react';

import { api } from '@/lib/api';
import { CHANNELS, type ChannelStatus } from '@/platform/registries';

import { DiscordConfig } from './DiscordConfig';
import { LarkConfig } from './LarkConfig';
import { NarramessengerConfig } from './NarramessengerConfig';
import { SlackConfig } from './SlackConfig';
import { TelegramConfig } from './TelegramConfig';
import { WeChatConfig } from './WeChatConfig';

/** Bound + enabled → active; bound but disabled → inactive; anything else (unbound, denied, network) → unbound. */
const probe = (channel: string) => async (agentId: string): Promise<ChannelStatus> => {
  try {
    const res = await api.channelCredential(channel, agentId);
    if (!res.success || !res.data) return 'unbound';
    return res.data.enabled ? 'active' : 'inactive';
  } catch {
    return 'unbound';
  }
};

// Each row is guarded on ITS OWN id, not a single `if` around all six: a shared guard keyed
// on 'lark' meant that once the lark row was removed (a disabled builtin, or a test that
// exercises `removeOwner`), re-evaluating this module would try to register all six again —
// five of which still exist under their own owners, so `CHANNELS.register` would throw
// `RegistryConflictError` for each and the whole Channels chunk would fail to load.
if (!CHANNELS.has('lark')) CHANNELS.register('lark', { label: 'Lark / Feishu', icon: MessageSquare, component: LarkConfig, order: 10, fetchStatus: probe('lark') }, { owner: 'builtin.channels.lark' });
if (!CHANNELS.has('slack')) CHANNELS.register('slack', { label: 'Slack', icon: Hash, component: SlackConfig, order: 20, fetchStatus: probe('slack') }, { owner: 'builtin.channels.slack' });
if (!CHANNELS.has('telegram')) CHANNELS.register('telegram', { label: 'Telegram', icon: Send, component: TelegramConfig, order: 30, fetchStatus: probe('telegram') }, { owner: 'builtin.channels.telegram' });
if (!CHANNELS.has('wechat')) CHANNELS.register('wechat', { label: 'WeChat', icon: QrCode, component: WeChatConfig, order: 40, fetchStatus: probe('wechat') }, { owner: 'builtin.channels.wechat' });
if (!CHANNELS.has('narramessenger')) CHANNELS.register('narramessenger', { label: 'NarraMessenger', icon: MessageCircle, component: NarramessengerConfig, order: 50, fetchStatus: probe('narramessenger') }, { owner: 'builtin.channels.narramessenger' });
if (!CHANNELS.has('discord')) CHANNELS.register('discord', { label: 'Discord', icon: Bot, component: DiscordConfig, order: 60, fetchStatus: probe('discord') }, { owner: 'builtin.channels.discord' });
