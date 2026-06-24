/**
 * WeChatConfig — Per-agent personal-WeChat (iLink) binding configuration.
 *
 * States:
 *   1. No account bound → "Connect" button → QR-scan flow:
 *        start → render login QR → poll until the user scans + confirms.
 *   2. Account bound    → connected status (owner / base url) + Unbind.
 *
 * Unlike the bot channels (Telegram / Slack / Lark) there is NO token to
 * paste — personal WeChat authenticates by scanning a login QR with the
 * phone, exactly like WeChat web. The bind is therefore two server calls:
 * `/qrcode/start` (get the QR) then a poll loop on `/qrcode/poll` that the
 * gateway long-polls; on "confirmed" the backend has already persisted the
 * iLink bot_token, so the UI just re-fetches the (sanitised) credential.
 *
 * The owner's wxid is opaque until the first inbound DM (the gateway never
 * reveals it at bind time), so right after binding the owner shows as
 * "pending" until the owner messages the account once — same first-contact
 * pattern as Telegram's @username trust signal.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  MessageSquare,
  Link,
  Unlink,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ExternalLink,
  XCircle,
} from 'lucide-react';

import { Card, CardHeader, CardTitle, CardContent, Button, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { WeChatCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

export function WeChatConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<WeChatCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [unbindLoading, setUnbindLoading] = useState(false);

  // QR-scan flow state.
  const [connecting, setConnecting] = useState(false);   // start request in flight
  const [qrUrl, setQrUrl] = useState('');                // scannable WeChat URL
  const [qrImgFailed, setQrImgFailed] = useState(false); // <img> couldn't render qrUrl
  const [polling, setPolling] = useState(false);         // poll loop is active

  const mountedRef = useRef(true);
  // Guards the recursive poll loop — flipping to false stops it (cancel/unmount).
  const pollingActiveRef = useRef(false);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getWeChatCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || 'Failed to load WeChat credential');
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Failed to fetch WeChat credential');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId]);

  const stopPolling = useCallback(() => {
    pollingActiveRef.current = false;
    if (mountedRef.current) {
      setPolling(false);
      setQrUrl('');
      setQrImgFailed(false);
    }
  }, []);

  useEffect(() => {
    setError('');
    setCredential(null);
    stopPolling();
    fetchCredential();
    // Stop any in-flight poll when the selected agent changes.
    return () => stopPolling();
  }, [fetchCredential, stopPolling]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollingActiveRef.current = false;
    };
  }, []);

  // Recursive poll: /qrcode/poll long-polls on the gateway side (~30s), so we
  // re-invoke immediately on "wait" — the server call IS the pacing. We never
  // tight-loop the client.
  const runPollLoop = useCallback(
    async (qrcode: string, baseUrl: string) => {
      while (pollingActiveRef.current) {
        let res;
        try {
          res = await api.pollWeChatQrcode(agentId, qrcode, baseUrl);
        } catch (e: unknown) {
          if (!pollingActiveRef.current) return;
          setError(e instanceof Error ? e.message : 'QR status poll failed');
          stopPolling();
          return;
        }
        if (!pollingActiveRef.current) return;
        if (!res.success) {
          setError(res.error || 'QR status poll failed');
          stopPolling();
          return;
        }
        if (res.data?.status === 'confirmed') {
          stopPolling();
          await fetchCredential();
          onBindStateChange?.();
          return;
        }
        // status === "wait" — long-poll expired with no scan; re-poll.
      }
    },
    [agentId, fetchCredential, onBindStateChange, stopPolling],
  );

  const handleConnect = async () => {
    if (!agentId) return;
    setConnecting(true);
    setError('');
    setQrImgFailed(false);
    try {
      const res = await api.startWeChatQrcode(agentId);
      if (!mountedRef.current) return;
      if (!res.success || !res.data?.qrcode || !res.data?.qr_url) {
        setError(res.error || 'Could not get a WeChat login QR');
        return;
      }
      setQrUrl(res.data.qr_url);
      setPolling(true);
      pollingActiveRef.current = true;
      // Fire-and-forget the loop; it self-guards on pollingActiveRef.
      void runPollLoop(res.data.qrcode, res.data.base_url || '');
    } catch (e: unknown) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : 'Could not start WeChat bind');
      }
    } finally {
      if (mountedRef.current) setConnecting(false);
    }
  };

  const handleUnbind = async () => {
    if (!agentId) return;
    const ok = await confirm({
      title: 'Disconnect WeChat?',
      message: 'The agent will stop receiving WeChat messages and lose all WeChat tools until you scan in again.',
      confirmText: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindWeChat(agentId);
      if (res.success) {
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || 'Disconnect failed');
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Disconnect failed');
    } finally {
      if (mountedRef.current) setUnbindLoading(false);
    }
  };

  return (
    <Card>
      {confirmDialog}
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" />
          WeChat
        </CardTitle>
        <button
          onClick={() => fetchCredential()}
          disabled={loading}
          className="p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div role="alert" className="flex items-center gap-2 text-sm text-[var(--color-red-500)] border border-[var(--color-red-500)] p-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            {error}
          </div>
        )}

        {/* State 1: No account bound */}
        {!credential && !polling && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              Connect your personal WeChat so the agent can chat with you in
              WeChat DMs. You'll scan a login QR with the WeChat app on your
              phone — no password or token needed.
            </p>
            <div className="text-xs text-[var(--color-yellow-500)]" role="note">
              ⚠ This signs the agent in as a personal WeChat account via a
              third-party gateway. Use an account you control; personal-account
              automation is outside WeChat's official Bot terms.
            </div>
            <Button
              onClick={handleConnect}
              disabled={connecting}
              className="w-full"
              size="sm"
            >
              {connecting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              Connect WeChat
            </Button>
          </div>
        )}

        {/* State 1b: QR shown, waiting for scan */}
        {!credential && polling && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              Open WeChat on your phone → <strong>Discover → Scan</strong>, and
              scan this code. Keep this panel open until it confirms.
            </p>
            <div className="flex flex-col items-center gap-2 border border-[var(--border-default)] rounded p-4">
              {qrUrl && !qrImgFailed ? (
                <img
                  src={qrUrl}
                  alt="WeChat login QR code"
                  className="w-44 h-44 object-contain bg-white p-1"
                  onError={() => setQrImgFailed(true)}
                />
              ) : (
                // Gateway returned a URL that isn't a direct image — surface it
                // as a link the user can open to render the QR.
                <a
                  href={qrUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-[var(--accent-primary)] hover:underline inline-flex items-center gap-1"
                >
                  Open login QR <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
              <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Waiting for scan…
              </span>
            </div>
            <Button
              onClick={stopPolling}
              variant="outline"
              size="sm"
              className="w-full"
            >
              <XCircle className="w-4 h-4 mr-2" />
              Cancel
            </Button>
          </div>
        )}

        {/* State 2: Account bound */}
        {credential && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.owner_name || credential.bot_wx_id || 'WeChat account'}
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> Connected
              </span>
            </div>
            {credential.owner_wx_id ? (
              <div className="text-xs text-[var(--text-secondary)]">
                Owner: <span className="text-[var(--text-primary)]">{credential.owner_name || credential.owner_wx_id}</span>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                ⏳ Owner registration pending — send any message to this account
                from your own WeChat once to activate the owner trust signal.
                <div className="mt-1 text-[var(--text-secondary)]">
                  (The gateway only reveals your wxid on a real DM, so the
                  signal activates on first contact, not at scan time.)
                </div>
              </div>
            )}
            <div className="flex gap-2">
              <Button
                onClick={handleUnbind}
                disabled={unbindLoading}
                variant="outline"
                size="sm"
                className="flex-1"
              >
                {unbindLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : (
                  <Unlink className="w-4 h-4 mr-2" />
                )}
                Disconnect
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
