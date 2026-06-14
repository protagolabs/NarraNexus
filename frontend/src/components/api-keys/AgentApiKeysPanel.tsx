/**
 * @file AgentApiKeysPanel.tsx
 * @description Owner-facing UI for managing the external API protocol
 *              (v0.3) api keys on a single agent.
 *
 * Shows a list of every nxk_ token the owner has minted for this agent
 * (active + revoked + expired). Lets the owner:
 *   - Create a new token (one-time plaintext reveal modal)
 *   - Rotate a token (mint a new one with 7-day grace on the old)
 *   - Revoke a token (soft delete; row stays for audit history)
 *
 * The plaintext token is shown ONCE inside a Dialog at create / rotate
 * time. After closing the modal there is no way to recover it — by design
 * (DB stores SHA256 only). The "Copy" button + a confirm step before
 * close help users not accidentally lose it.
 *
 * Styling: uses NM-native CSS variables (`var(--nm-…)` / `var(--bg-…)` /
 * `var(--border-…)` / `var(--text-…)` / `var(--color-…)`). Do NOT
 * reintroduce shadcn-style class tokens (`bg-card`, `bg-muted`,
 * `text-muted-foreground`, `bg-destructive`, `border-border`,
 * `border-input`, `bg-background`) — they are not defined in this
 * project's Tailwind v4 `@theme` block and render as transparent /
 * invisible (this caused the "modal text overlaps list" bug).
 */

import { useState, useEffect, useCallback } from 'react';
import { Loader2, Plus, RefreshCw, Trash2, KeyRound, Copy, Check, AlertTriangle } from 'lucide-react';
import { Button, Dialog, DialogContent, DialogFooter } from '@/components/ui';
import { api } from '@/lib/api';
import type { ApiKeyInfo } from '@/types';

interface Props {
  agentId: string;
}

interface CreateModalState {
  open: boolean;
  plaintext?: string;
  keyName?: string;
  isRotation?: boolean;
}

const DEFAULT_SCOPES = ['chat', 'session.delete', 'session.list'];

export function AgentApiKeysPanel({ agentId }: Props) {
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newKeyName, setNewKeyName] = useState('');
  const [busy, setBusy] = useState<string | null>(null); // key_id being mutated
  const [createModal, setCreateModal] = useState<CreateModalState>({ open: false });
  const [copied, setCopied] = useState(false);

  // ── Load list ─────────────────────────────────────────────────────────

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.listAgentApiKeys(agentId);
      if (resp.success) {
        setKeys(resp.keys ?? []);
      } else {
        setError(resp.error ?? 'Failed to load API keys');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    if (agentId) refresh();
  }, [agentId, refresh]);

  // ── Create ────────────────────────────────────────────────────────────

  const handleCreate = async () => {
    if (!newKeyName.trim()) return;
    setBusy('__create__');
    try {
      const resp = await api.createAgentApiKey(agentId, {
        name: newKeyName.trim(),
        scopes: DEFAULT_SCOPES,
      });
      if (resp.success && resp.plaintext_token) {
        setCreateModal({
          open: true,
          plaintext: resp.plaintext_token,
          keyName: newKeyName.trim(),
        });
        setNewKeyName('');
        await refresh();
      } else {
        setError(resp.error ?? 'Create failed');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // ── Rotate ────────────────────────────────────────────────────────────

  const handleRotate = async (key: ApiKeyInfo) => {
    if (!confirm(`Rotate "${key.name}"?\n\nA new token will be issued. The old token will continue working for 7 days, then expire.`)) {
      return;
    }
    setBusy(key.key_id);
    try {
      const resp = await api.rotateAgentApiKey(agentId, key.key_id);
      if (resp.success && resp.plaintext_token) {
        setCreateModal({
          open: true,
          plaintext: resp.plaintext_token,
          keyName: key.name,
          isRotation: true,
        });
        await refresh();
      } else {
        setError(resp.error ?? 'Rotate failed');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // ── Revoke ────────────────────────────────────────────────────────────

  const handleRevoke = async (key: ApiKeyInfo) => {
    if (!confirm(`Revoke "${key.name}"?\n\nThis cannot be undone. The token will return 401 immediately. Audit history is preserved.`)) {
      return;
    }
    setBusy(key.key_id);
    try {
      const resp = await api.revokeAgentApiKey(agentId, key.key_id);
      if (resp.success) {
        await refresh();
      } else {
        setError(resp.error ?? 'Revoke failed');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // ── Plaintext copy ────────────────────────────────────────────────────

  const handleCopy = async () => {
    if (!createModal.plaintext) return;
    try {
      await navigator.clipboard.writeText(createModal.plaintext);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore — user can also select + copy by hand
    }
  };

  const closePlaintextModal = () => {
    if (!confirm('Have you copied and stored the token? It will not be shown again.')) {
      return;
    }
    setCreateModal({ open: false });
    setCopied(false);
  };

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            External API Access
          </h2>
          <p className="text-xs mt-1" style={{ color: 'var(--text-tertiary)' }}>
            Mint <code className="font-mono">nxk_</code> tokens so external apps can call
            this agent via <code className="font-mono">/v1/external/chat/completions</code>.
            Each token is scoped permanently to this agent.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={loading ? 'animate-spin' : ''} size={14} />
        </Button>
      </header>

      {error && (
        <div
          className="rounded p-3 text-sm flex items-start gap-2"
          style={{
            background: 'color-mix(in srgb, var(--color-error) 10%, transparent)',
            border: '1px solid color-mix(in srgb, var(--color-error) 50%, transparent)',
            color: 'var(--color-error)',
          }}
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Create form */}
      <div className="flex gap-2 items-center">
        <input
          type="text"
          placeholder='Token name (e.g. "Arena prod")'
          value={newKeyName}
          onChange={(e) => setNewKeyName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && newKeyName.trim()) handleCreate();
          }}
          className="flex-1 px-3 py-1.5 rounded text-sm"
          style={{
            border: '1px solid var(--border-default)',
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
          }}
          disabled={busy === '__create__'}
        />
        <Button
          size="sm"
          onClick={handleCreate}
          disabled={!newKeyName.trim() || busy === '__create__'}
        >
          {busy === '__create__' ? (
            <Loader2 className="animate-spin" size={14} />
          ) : (
            <Plus size={14} />
          )}
          Create
        </Button>
      </div>

      {/* Key list */}
      {loading && keys.length === 0 ? (
        <div className="text-center text-sm py-6" style={{ color: 'var(--text-tertiary)' }}>
          <Loader2 className="animate-spin mx-auto mb-2" size={16} />
          Loading…
        </div>
      ) : keys.length === 0 ? (
        <div className="text-center text-sm py-6" style={{ color: 'var(--text-tertiary)' }}>
          No API keys yet. Mint one above to get started.
        </div>
      ) : (
        <ul className="space-y-2">
          {keys.map((k) => (
            <li
              key={k.key_id}
              className="rounded p-3 flex items-start justify-between gap-3"
              style={{ border: '1px solid var(--border-default)', background: 'var(--bg-primary)' }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <KeyRound size={14} style={{ color: 'var(--text-tertiary)' }} />
                  <span className="font-medium text-sm" style={{ color: 'var(--text-primary)' }}>
                    {k.name}
                  </span>
                  <StatusBadge status={k.status} />
                </div>
                <div className="text-xs mt-1 font-mono" style={{ color: 'var(--text-tertiary)' }}>
                  {k.token_prefix}…
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--text-tertiary)' }}>
                  Scopes: {k.scopes.join(', ')}
                </div>
                <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                  Last used: {k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'never'} ·
                  Created: {k.created_at ? new Date(k.created_at).toLocaleString() : '—'}
                </div>
              </div>

              <div className="flex flex-col gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRotate(k)}
                  disabled={k.status !== 'active' || busy === k.key_id}
                  title="Rotate (issue new + 7-day grace on this one)"
                >
                  {busy === k.key_id ? (
                    <Loader2 className="animate-spin" size={12} />
                  ) : (
                    <RefreshCw size={12} />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRevoke(k)}
                  disabled={k.status === 'revoked' || busy === k.key_id}
                  style={{ color: 'var(--color-error)' }}
                  title="Revoke (immediate 401)"
                >
                  <Trash2 size={12} />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Plaintext reveal modal — uses the canonical NM Dialog so it
          portals to <body>, gets an opaque NM-raised background, sits at
          z-[1000]/[1001], and can't bleed through into the panel below. */}
      <Dialog
        isOpen={createModal.open && !!createModal.plaintext}
        onClose={closePlaintextModal}
        title={createModal.isRotation ? 'Token rotated' : 'Token created'}
        size="xl"
      >
        <DialogContent>
          <div className="space-y-4">
            <div
              className="rounded p-3 text-xs leading-relaxed"
              style={{
                background: 'color-mix(in srgb, var(--color-warning) 12%, transparent)',
                border: '1px solid color-mix(in srgb, var(--color-warning) 45%, transparent)',
                color: 'var(--text-primary)',
              }}
            >
              <strong>Save this now.</strong> This is the only time you'll see the full
              token. There is no way to recover it later — only revoke + create a new one.
              {createModal.isRotation && (
                <div className="mt-2">
                  Your old token still works for 7 days. Update your integrator's secret
                  before then.
                </div>
              )}
            </div>

            <div>
              <label className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
                <code className="font-mono">{createModal.keyName}</code>
              </label>
              <div className="mt-1 flex gap-2 items-center">
                <code
                  className="flex-1 px-3 py-2 rounded font-mono text-xs break-all"
                  style={{
                    background: 'var(--bg-elevated)',
                    border: '1px solid var(--border-subtle)',
                    color: 'var(--text-primary)',
                  }}
                >
                  {createModal.plaintext}
                </code>
                <Button size="sm" onClick={handleCopy}>
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? 'Copied' : 'Copy'}
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button variant="outline" onClick={closePlaintextModal}>
            I've saved it
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}

function StatusBadge({ status }: { status: ApiKeyInfo['status'] }) {
  const styles: Record<ApiKeyInfo['status'], { bg: string; fg: string; border: string }> = {
    active: {
      bg: 'color-mix(in srgb, var(--color-success) 15%, transparent)',
      fg: 'var(--color-success)',
      border: 'color-mix(in srgb, var(--color-success) 35%, transparent)',
    },
    expired: {
      bg: 'color-mix(in srgb, var(--color-warning) 15%, transparent)',
      fg: 'var(--color-warning)',
      border: 'color-mix(in srgb, var(--color-warning) 35%, transparent)',
    },
    revoked: {
      bg: 'color-mix(in srgb, var(--color-error) 15%, transparent)',
      fg: 'var(--color-error)',
      border: 'color-mix(in srgb, var(--color-error) 35%, transparent)',
    },
  };
  const s = styles[status];
  return (
    <span
      className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded"
      style={{ background: s.bg, color: s.fg, border: `1px solid ${s.border}` }}
    >
      {status}
    </span>
  );
}
