/**
 * LarkConfig — Per-agent Lark/Feishu bot binding configuration.
 *
 * States:
 *   1. No bot bound → show bind form (App ID, Secret, Platform)
 *   2. Bot bound, not logged in → show login button
 *   3. Bot bound, logged in → show connected status + unbind
 */

import { useState, useEffect, useCallback } from 'react';
import { MessageSquare, Link, Unlink, ExternalLink, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Input } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

interface LarkCredential {
  agent_id: string;
  app_id: string;
  brand: string;
  bot_name: string;
  owner_open_id: string;
  owner_name: string;
  auth_status: string;
  is_active: boolean;
}

export function LarkConfig() {
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<LarkCredential | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState('');

  // Bind form state
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [ownerEmail, setOwnerEmail] = useState('');
  const [brand, setBrand] = useState<'feishu' | 'lark'>('feishu');

  // OAuth state
  const [authUrl, setAuthUrl] = useState('');
  const [deviceCode, setDeviceCode] = useState('');
  const [polling, setPolling] = useState(false);

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      const res = await api.getLarkCredential(agentId);
      if (res.success) {
        setCredential(res.data || null);
      }
    } catch (e: any) {
      console.error('Failed to fetch Lark credential:', e);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    fetchCredential();
  }, [fetchCredential]);

  // Bind bot
  const handleBind = async () => {
    if (!agentId || !appId || !appSecret) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.bindLarkBot(agentId, appId, appSecret, brand, ownerEmail);
      if (res.success) {
        setAppId('');
        setAppSecret('');
        await fetchCredential();
      } else {
        setError(res.error || 'Failed to bind bot');
      }
    } catch (e: any) {
      setError(e.message || 'Failed to bind bot');
    } finally {
      setActionLoading(false);
    }
  };

  // Start OAuth login
  const handleLogin = async () => {
    if (!agentId) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.larkAuthLogin(agentId);
      if (res.success) {
        const data = res.data || {};
        const url = data.verification_url || data.verification_uri || '';
        const code = data.device_code || data.user_code || '';
        if (url) {
          setAuthUrl(url);
          setDeviceCode(code);
          window.open(url, '_blank');
          // Start polling for completion
          if (code) {
            startPolling(code);
          }
        }
      } else {
        setError(res.error || 'Failed to initiate login');
      }
    } catch (e: any) {
      setError(e.message || 'Failed to initiate login');
    } finally {
      setActionLoading(false);
    }
  };

  // Poll for OAuth completion
  const startPolling = (code: string) => {
    setPolling(true);
    const interval = setInterval(async () => {
      try {
        const res = await api.larkAuthComplete(agentId!, code);
        if (res.success) {
          clearInterval(interval);
          setPolling(false);
          setAuthUrl('');
          setDeviceCode('');
          await fetchCredential();
        }
      } catch {
        // Keep polling — auth not complete yet
      }
    }, 3000);

    // Stop after 5 minutes
    setTimeout(() => {
      clearInterval(interval);
      setPolling(false);
    }, 300000);
  };

  // Unbind bot
  const handleUnbind = async () => {
    if (!agentId) return;
    setActionLoading(true);
    setError('');
    try {
      const res = await api.unbindLarkBot(agentId);
      if (res.success) {
        setCredential(null);
      } else {
        setError(res.error || 'Failed to unbind');
      }
    } catch (e: any) {
      setError(e.message || 'Failed to unbind');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><MessageSquare className="w-4 h-4" /> Lark / Feishu</CardTitle></CardHeader>
        <CardContent><div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div></CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" />
          Lark / Feishu
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div className="flex items-center gap-2 text-sm text-red-400 bg-red-400/10 p-2 rounded">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* State 1: No bot bound */}
        {!credential && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              Bind a Feishu/Lark bot to enable messaging, contacts, docs, calendar, and tasks.
            </p>
            <div className="space-y-2">
              <Input
                placeholder="App ID (e.g. cli_xxx)"
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                className="text-sm"
              />
              <Input
                type="password"
                placeholder="App Secret"
                value={appSecret}
                onChange={(e) => setAppSecret(e.target.value)}
                className="text-sm"
              />
              <Input
                placeholder="Your Lark account email"
                value={ownerEmail}
                onChange={(e) => setOwnerEmail(e.target.value)}
                className="text-sm"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => setBrand('feishu')}
                  className={`flex-1 py-1.5 px-3 text-xs rounded border transition-colors ${
                    brand === 'feishu'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]'
                      : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--text-secondary)]'
                  }`}
                >
                  Feishu
                </button>
                <button
                  onClick={() => setBrand('lark')}
                  className={`flex-1 py-1.5 px-3 text-xs rounded border transition-colors ${
                    brand === 'lark'
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]'
                      : 'border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--text-secondary)]'
                  }`}
                >
                  Lark (International)
                </button>
              </div>
            </div>
            <Button
              onClick={handleBind}
              disabled={actionLoading || !appId || !appSecret}
              className="w-full"
              size="sm"
            >
              {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              Bind Bot
            </Button>
          </div>
        )}

        {/* State 2: Bot bound, not logged in */}
        {credential && credential.auth_status !== 'logged_in' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)]">{credential.app_id}</span>
                <span className="text-[var(--text-secondary)] ml-2">({credential.brand})</span>
              </div>
              <span className="flex items-center gap-1 text-xs text-yellow-400">
                <AlertCircle className="w-3 h-3" /> Not logged in
              </span>
            </div>

            {polling ? (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <Loader2 className="w-4 h-4 animate-spin" />
                Waiting for authorization...
                {authUrl && (
                  <a href={authUrl} target="_blank" rel="noopener noreferrer" className="text-[var(--accent-primary)] hover:underline">
                    <ExternalLink className="w-3 h-3 inline" /> Open
                  </a>
                )}
              </div>
            ) : (
              <Button onClick={handleLogin} disabled={actionLoading} size="sm" className="w-full">
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <ExternalLink className="w-4 h-4 mr-2" />}
                Login with {credential.brand === 'feishu' ? 'Feishu' : 'Lark'}
              </Button>
            )}

            <Button onClick={handleUnbind} disabled={actionLoading} variant="ghost" size="sm" className="w-full text-red-400 hover:text-red-300">
              <Unlink className="w-4 h-4 mr-2" /> Unbind
            </Button>
          </div>
        )}

        {/* State 3: Bot bound and logged in */}
        {credential && credential.auth_status === 'logged_in' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.bot_name || credential.app_id}
                </span>
                <span className="text-[var(--text-secondary)] ml-2">
                  ({credential.brand === 'feishu' ? 'Feishu' : 'Lark'})
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-green-400">
                <CheckCircle className="w-3 h-3" /> Connected
              </span>
            </div>

            {credential.owner_name && (
              <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] bg-[var(--bg-tertiary)] p-2 rounded">
                <CheckCircle className="w-3 h-3 text-green-400 flex-shrink-0" />
                Linked as: <span className="text-[var(--text-primary)] font-medium">{credential.owner_name}</span>
              </div>
            )}

            <div className="text-xs text-[var(--text-secondary)]">
              App ID: {credential.app_id}
            </div>

            <Button onClick={handleUnbind} disabled={actionLoading} variant="ghost" size="sm" className="w-full text-red-400 hover:text-red-300">
              <Unlink className="w-4 h-4 mr-2" /> Unbind
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
