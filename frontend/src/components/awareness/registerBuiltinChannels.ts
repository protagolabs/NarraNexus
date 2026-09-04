/**
 * @file_name: registerBuiltinChannels.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The six builtin IM channels' rows in the `ui.channels` registry, each owned by its channel plugin.
 *
 * Imported by `IMChannelsSection` (the registration side effect runs once).
 * Owners are the builtin plugin ids so `disableBuiltinUi('builtin.channels.<x>')`
 * removes the row together with the channel's backend contributions.
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

if (!CHANNELS.has('lark')) {
  CHANNELS.register('lark', { label: 'Lark / Feishu', icon: MessageSquare, component: LarkConfig, order: 10, fetchStatus: probe('lark') }, { owner: 'builtin.channels.lark' });
  CHANNELS.register('slack', { label: 'Slack', icon: Hash, component: SlackConfig, order: 20, fetchStatus: probe('slack') }, { owner: 'builtin.channels.slack' });
  CHANNELS.register('telegram', { label: 'Telegram', icon: Send, component: TelegramConfig, order: 30, fetchStatus: probe('telegram') }, { owner: 'builtin.channels.telegram' });
  CHANNELS.register('wechat', { label: 'WeChat', icon: QrCode, component: WeChatConfig, order: 40, fetchStatus: probe('wechat') }, { owner: 'builtin.channels.wechat' });
  CHANNELS.register('narramessenger', { label: 'NarraMessenger', icon: MessageCircle, component: NarramessengerConfig, order: 50, fetchStatus: probe('narramessenger') }, { owner: 'builtin.channels.narramessenger' });
  CHANNELS.register('discord', { label: 'Discord', icon: Bot, component: DiscordConfig, order: 60, fetchStatus: probe('discord') }, { owner: 'builtin.channels.discord' });
}
