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

const probe = (fetch: () => Promise<{ success: boolean; data?: unknown }>, active: (d: unknown) => boolean) => async (): Promise<ChannelStatus> => {
  try {
    const res = await fetch();
    if (!res.success || !res.data) return 'unbound';
    return active(res.data) ? 'active' : 'inactive';
  } catch {
    return 'unbound';
  }
};

const enabled = (d: unknown) => Boolean((d as { enabled?: boolean }).enabled);

if (!CHANNELS.has('lark')) {
  CHANNELS.register('lark', { label: 'Lark / Feishu', icon: MessageSquare, component: LarkConfig, order: 10, fetchStatus: (agentId) => probe(() => api.getLarkCredential(agentId), (d) => Boolean((d as { is_active?: boolean }).is_active))() }, { owner: 'builtin.channels.lark' });
  CHANNELS.register('slack', { label: 'Slack', icon: Hash, component: SlackConfig, order: 20, fetchStatus: (agentId) => probe(() => api.getSlackCredential(agentId), enabled)() }, { owner: 'builtin.channels.slack' });
  CHANNELS.register('telegram', { label: 'Telegram', icon: Send, component: TelegramConfig, order: 30, fetchStatus: (agentId) => probe(() => api.getTelegramCredential(agentId), enabled)() }, { owner: 'builtin.channels.telegram' });
  CHANNELS.register('wechat', { label: 'WeChat', icon: QrCode, component: WeChatConfig, order: 40, fetchStatus: (agentId) => probe(() => api.getWeChatCredential(agentId), enabled)() }, { owner: 'builtin.channels.wechat' });
  CHANNELS.register('narramessenger', { label: 'NarraMessenger', icon: MessageCircle, component: NarramessengerConfig, order: 50, fetchStatus: (agentId) => probe(() => api.getNarramessengerCredential(agentId), enabled)() }, { owner: 'builtin.channels.narramessenger' });
  CHANNELS.register('discord', { label: 'Discord', icon: Bot, component: DiscordConfig, order: 60, fetchStatus: (agentId) => probe(() => api.getDiscordCredential(agentId), enabled)() }, { owner: 'builtin.channels.discord' });
}
