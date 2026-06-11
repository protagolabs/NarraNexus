/**
 * @file_name: tokenInbound.ts
 * @description: Power login-state pass-through (scenario A). When the page
 * is opened with ?token=<NetMind loginToken> (e.g. a link from netmind.ai
 * or Arena), take the token, strip it from the URL immediately (avoid
 * leaking it into history), and exchange it for our session. `source` is
 * read alongside and forwarded for downstream provisioning (Phase 2).
 */
import { api } from '@/lib/api';

export interface InboundResult { handled: boolean; token?: string; source?: string }

/** Parse + strip ?token=/?source= from the URL. Returns what was found. */
export function takeInboundToken(loc: { search: string; pathname: string; hash: string }): InboundResult {
  const params = new URLSearchParams(loc.search);
  const token = params.get('token');
  const source = params.get('source') || undefined;
  if (!token) return { handled: false, source };
  params.delete('token');
  const rest = params.toString();
  const newUrl = loc.pathname + (rest ? `?${rest}` : '') + loc.hash;
  window.history.replaceState(null, '', newUrl);
  return { handled: true, token, source };
}

/** Exchange an inbound NetMind token for our session. Returns the response. */
export async function exchangeInboundToken(token: string, source?: string) {
  return api.netmindLogin(token, source);
}
