/**
 * @file ProviderSettings.tsx
 * @description LLM Providers — the credential WALLET (Settings › LLM Providers).
 *
 * Layout (a card grid + two modals):
 *
 *   ┌─────────────────────────────────────────┐
 *   │  Your providers          [Update models] │
 *   │  ┌ provider card ┐ ┌ provider card ┐     │
 *   │  ┌ provider card ┐ ┌ + Add provider ┐    │
 *   └─────────────────────────────────────────┘
 *   • click a provider card → detail modal (models, masked key, endpoint,
 *     Test / Edit / Delete)
 *   • "+ Add provider" card → add modal with 3 methods: OAuth sign-in
 *     (Claude Code / Codex CLI), one-key preset, custom endpoint.
 *
 * The GLOBAL DEFAULT model/framework does NOT live here anymore — it moved to
 * the "Model Defaults" nav section (ModelDefaultsSettings). Per-agent overrides
 * live in the chat page.
 *
 * Uses the bioluminescent terminal design system CSS variables.
 */

import { useState, useEffect, useCallback, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { OneKeyOnboard } from './OneKeyOnboard'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { useConfigStore } from '@/stores'
import { getApiBaseUrl } from '@/stores/runtimeStore'
import { Dialog, DialogContent, DialogFooter } from '@/components/ui'
import { api } from '@/lib/api'
import { MODEL_SUGGESTION_GROUPS } from '@/lib/agentFramework'
import { ModelBubbleInput } from '@/components/providers/ModelBubbleInput'
import { CustomEndpointForm } from '@/components/providers/CustomEndpointForm'
import { CliSignInPanel } from '@/components/providers/CliSignInPanel'

/** fetch wrapper that injects the identity headers configStore tracks.
 *
 * Two headers, mutually compatible (mirror of ApiClient.getAuthHeaders):
 *   - Authorization: Bearer <jwt>  — cloud mode signed identity
 *   - X-User-Id: <user_id>         — local mode unsigned identity
 *
 * Sending both is intentional. Backend auth_middleware picks the right
 * one for the active mode and ignores the other (defence in depth: a
 * cloud server won't honour X-User-Id even if a client sets it).
 *
 * History: until 2026-05-18 this wrapper only sent the JWT, which
 * silently broke local mode. Settings calls landed under whatever user
 * the backend's "first row in users" fallback resolved to (the eldest
 * account), so a freshly-registered user's API key + slot bindings got
 * written to someone else's row. Now we always send X-User-Id and the
 * backend has lost the dangerous fallback — see auth.py 2026-05-18 note. */
function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  try {
    const raw = localStorage.getItem('narra-nexus-config')
    if (raw) {
      const state = JSON.parse(raw)?.state || {}
      if (state.token) headers.set('Authorization', `Bearer ${state.token}`)
      if (state.userId) headers.set('X-User-Id', state.userId)
    }
  } catch {
    // Corrupt/absent localStorage config — proceed without auth headers;
    // the backend 401s if the request actually needed them.
  }
  return fetch(input, { ...init, headers })
}

// =============================================================================
// Types
// =============================================================================

interface ProviderSummary {
  provider_id: string
  name: string
  source: string
  protocol: string
  auth_type: string
  is_active: boolean
  models: string[]
  api_key_masked?: string
  base_url?: string
  // NetMind account this key belongs to (captured at mint). Lets the user tell
  // several keys from one broke account apart and top up the right one.
  netmind_account_email?: string
}


// Preset quick-add moved to the shared OneKeyOnboard component (one-key
// setup via POST /api/providers/onboard) — the provider list, Get Key
// URLs, and recommended default models now live there / in
// model_catalog._ONBOARD_*_MODELS.

// MODEL_SUGGESTION_GROUPS (imported above) powers the custom-provider form's
// model bubble input. The framework/slot machinery that used to live here moved
// out with the global default → ModelDefaultsSettings.

// =============================================================================
// Helpers
// =============================================================================

// =============================================================================
// Section Header
// =============================================================================

function SectionHeader({ step, title, subtitle, action }: { step?: number; title: string; subtitle: string; action?: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <div className="flex items-baseline gap-3 min-w-0">
          {step != null && (
            <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em] text-[var(--text-tertiary)] tabular-nums">
              {String(step).padStart(2, '0')}
            </span>
          )}
          <h3 className="text-base font-[family-name:var(--font-display)] font-semibold text-[var(--text-primary)] tracking-tight">
            {title}
          </h3>
        </div>
        {action && <div className="shrink-0 self-center">{action}</div>}
      </div>
      <p className={cn('text-sm text-[var(--text-tertiary)] leading-relaxed', step != null && 'ml-[44px]')}>{subtitle}</p>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

// Security note (2026-06-17 → re-enabled 2026-07-09, Owner-authorized): custom
// endpoints are a first-class add method (the "Custom" tab). A user-supplied
// base_url routes the agent's LLM traffic to a host they choose — the tradeoff
// the original hardening flagged; kept visible here.

export function ProviderSettings() {
  const { t } = useTranslation()
  const userId = useConfigStore((s) => s.userId)

  /** Build a provider API URL. Identity travels in headers (X-User-Id in
   * local, JWT in cloud) — not the query string. The previous version
   * appended `?user_id=...` which the backend used to fall back to when
   * the X-User-Id header was missing; that turned the URL into a second,
   * unsigned identity channel and made cross-user write/read bugs easy
   * to trigger. Backend now requires identity from headers only.
   *
   * IMPORTANT: getApiBaseUrl() is called INSIDE the callback (not captured at
   * component mount), so it always reflects the current mode. When the user
   * switches between local and cloud, every fresh call returns the right host
   * without needing to re-mount this component. */
  const providerUrl = useCallback((path: string = '') => {
    return `${getApiBaseUrl()}/api/providers${path}`
  // userId is intentionally a dependency: re-creating the callback on
  // user switch is cheap and forces all consumers (refreshConfig etc.)
  // to re-run under the new identity.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId])

  const [providers, setProviders] = useState<Record<string, ProviderSummary>>({})

  const [syncing, setSyncing] = useState(false)
  // Inline summary line for the sync-defaults action: success / error / null.
  // Cleared whenever the user re-runs the sync so the UI never lies.
  const [syncResult, setSyncResult] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // Testing
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; msg: string }>>({})


  // Edit-models dialog. We only support editing the models list (backend has
  // PUT /{id}/models) — name / url / key changes aren't exposed, so the
  // dialog deliberately only shows the ModelBubbleInput + suggestions.
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null)
  const [editModels, setEditModels] = useState<string[]>([])
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState('')

  // Card-grid modals: the "+ Add provider" card opens the add modal (3 methods),
  // and clicking a provider card opens its detail modal.
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [detailProviderId, setDetailProviderId] = useState<string | null>(null)
  // Add-provider modal is a two-step wizard: 'menu' shows the three methods,
  // then the chosen one fills the modal (with a back link). Avoids the old
  // "everything stacked at once" wall — especially the custom form.
  const [addMethod, setAddMethod] = useState<'onekey' | 'oauth' | 'custom'>('onekey')

  // ---- Data loading ----
  const refreshConfig = useCallback(async () => {
    try {
      const cfgRes = await authFetch(providerUrl()).then((r) => r.json())
      if (cfgRes.success) setProviders(cfgRes.data.providers)
    } catch (err) {
      console.error('[ProviderSettings] refreshConfig failed:', err)
    }
  }, [providerUrl])

  useEffect(() => { refreshConfig() }, [refreshConfig])

  const providerList = Object.values(providers)
  const hasProviders = providerList.length > 0
  // The platform-funded card. Its presence is what makes the free-tier
  // explainer relevant — a bring-your-own-key user should not read it.
  const hasFreeTierCard = providerList.some((p) => p.source === 'netmind_free')

  // ---- Provider actions ----
  const handleDelete = async (id: string) => {
    await authFetch(providerUrl(`/${id}`), { method: 'DELETE' })
    await refreshConfig()
  }

  const handleSyncDefaults = async () => {
    if (!userId || syncing) return
    setSyncing(true)
    setSyncResult(null)
    try {
      const resp = await api.syncProviderDefaults()
      if (!resp.success) {
        setSyncResult({ kind: 'err', text: t('settings.provider.syncFailed') })
        return
      }
      if (resp.providers_updated === 0) {
        setSyncResult({ kind: 'ok', text: t('settings.provider.syncNothing') })
        return
      }
      const lines = resp.updates.map(u => `${u.name}: +${u.added.length} (${u.added.join(', ')})`)
      setSyncResult({
        kind: 'ok',
        text: `${t('settings.provider.syncUpdated', { providers: resp.providers_updated, models: resp.total_models_added })}\n${lines.join('\n')}`,
      })
      await refreshConfig()
    } catch (e) {
      setSyncResult({ kind: 'err', text: t('settings.provider.syncFailedError', { error: e instanceof Error ? e.message : String(e) }) })
    } finally {
      setSyncing(false)
    }
  }

  const openEditModels = (prov: ProviderSummary) => {
    setEditingProviderId(prov.provider_id)
    setEditModels([...prov.models])
    setEditError('')
  }
  const closeEditModels = () => {
    setEditingProviderId(null)
    setEditModels([])
    setEditError('')
  }
  const saveEditModels = async () => {
    if (!editingProviderId) return
    setEditSaving(true)
    setEditError('')
    try {
      const res = await authFetch(providerUrl(`/${editingProviderId}/models`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models: editModels }),
      }).then((r) => r.json())
      if (res.success) {
        await refreshConfig()
        closeEditModels()
      } else {
        setEditError(res.detail || t('settings.provider.updateModelsFailed'))
      }
    } catch {
      setEditError(t('settings.provider.networkError'))
    }
    setEditSaving(false)
  }

  const handleTest = async (id: string) => {
    setTesting(id)
    try {
      const res = await authFetch(providerUrl(`/${id}/test`), { method: 'POST' }).then((r) => r.json())
      setTestResults((p) => ({ ...p, [id]: { ok: res.success, msg: res.message } }))
    } catch {
      setTestResults((p) => ({ ...p, [id]: { ok: false, msg: t('settings.provider.networkError') } }))
    }
    setTesting(null)
  }

  // Local slot change. Preserves the slot's reasoning params: switching
  // provider/model must not silently reset Thinking/Reasoning Effort.

  // ---- Full view (always expanded) ----
  return (
    <div className="space-y-8">

      {/* ================================================================= */}
      {/* ① Your providers — the configured list, at the TOP. Claude Code    */}
      {/*    Login / Codex CLI Login are provider types too: they show here   */}
      {/*    once added, and as sign-in options in "Add a provider" below.    */}
      {/* ================================================================= */}
      <div>
        <SectionHeader
          title={t('settings.provider.providersListTitle')}
          subtitle={t('settings.provider.providersListSubtitle')}
          action={
            hasProviders ? (
              // "Update available models" — maintenance on the existing
              // providers (backfills the latest default model lists). A header
              // action, not an add step: hover → what it does, click → run it.
              <TooltipProvider delayDuration={200}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={handleSyncDefaults}
                      disabled={syncing || !userId}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors"
                    >
                      <RefreshCw className={cn('w-3.5 h-3.5', syncing && 'animate-spin')} />
                      {syncing ? t('settings.provider.syncing') : t('settings.provider.updateModelsBtn')}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="left" className="max-w-[280px]">
                    {t('settings.provider.updateModelsDesc')}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : undefined
          }
        />
        {/* Free-tier explainer. Users kept asking two things the card grid
            cannot answer on its own: how do I actually start using my own key,
            and do I have to burn the free credit first (no). Placed above the
            grid so it is read before someone goes looking for a Delete button
            on the free card — which is exactly what we block. */}
        {hasFreeTierCard && (
          <div className="mb-4 rounded-[var(--radius-xl)] border border-[var(--border-default)] bg-[var(--bg-tertiary)]/40 px-4 py-3 text-xs leading-relaxed text-[var(--text-secondary)] space-y-1.5">
            <div className="font-medium text-[var(--text-primary)]">
              {t('settings.provider.freeTierHowToTitle')}
            </div>
            <div>{t('settings.provider.freeTierHowToSwitch')}</div>
            <div>{t('settings.provider.freeTierAnytime')}</div>
            <div className="text-[var(--text-tertiary)]">
              {t('settings.provider.freeTierNoDelete')}
            </div>
          </div>
        )}
        {syncResult && (
          <p
            className={cn(
              'text-xs whitespace-pre-wrap leading-relaxed mb-3 ml-[34px] -mt-2',
              syncResult.kind === 'ok'
                ? 'text-[var(--text-secondary)]'
                : 'text-[var(--color-error)]'
            )}
          >
            {syncResult.text}
          </p>
        )}
        <div className="ml-[34px]">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {providerList.map((prov) => (
              <button
                key={prov.provider_id}
                type="button"
                onClick={() => setDetailProviderId(prov.provider_id)}
                className="text-left p-4 rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--bg-primary)] hover:border-[var(--accent-primary)]/40 transition-colors"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-sm font-medium text-[var(--text-primary)] truncate">{prov.name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase shrink-0">{prov.protocol}</span>
                </div>
                <div className="text-xs text-[var(--text-tertiary)] truncate">
                  {prov.api_key_masked || prov.source} · {t('settings.provider.modelsCount', { count: prov.models.length })}
                </div>
              </button>
            ))}
            {/* + Add provider card — opens the 3-method add modal. */}
            <button
              type="button"
              onClick={() => { setAddMethod('onekey'); setAddModalOpen(true) }}
              className="flex flex-col items-center justify-center gap-1 p-4 rounded-[var(--radius-xl)] border border-dashed border-[var(--border-default)] text-[var(--text-tertiary)] hover:border-[var(--accent-primary)]/50 hover:text-[var(--text-secondary)] transition-colors min-h-[76px]"
            >
              <Plus className="w-5 h-5" />
              <span className="text-sm">{t('settings.provider.addProviderTitle')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* ================================================================= */}
      {/* ② Add a provider — a modal opened from the "+ Add provider" grid card.
          Three methods: OAuth sign-in (Claude Code / Codex CLI), the one-key
          preset, and a custom endpoint. */}
      <Dialog
        isOpen={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        title={t('settings.provider.addProviderTitle')}
        size="2xl"
      >
        <DialogContent>
          {/* Tabs — three ways to add, switched in place (no wizard menu). */}
          <div className="flex gap-1 border-b border-[var(--border-subtle)] mb-4">
            {([
              { id: 'onekey', label: t('settings.provider.tabApiKey') },
              { id: 'oauth', label: t('settings.provider.tabSignin') },
              { id: 'custom', label: t('settings.provider.tabCustom') },
            ] as const).map((tb) => (
              <button
                key={tb.id}
                type="button"
                onClick={() => setAddMethod(tb.id)}
                className={cn(
                  'px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
                  addMethod === tb.id
                    ? 'border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                )}
              >
                {tb.label}
              </button>
            ))}
          </div>
          <div className="space-y-4">
          {/* API key — one-key preset dropdown + paste key. */}
          {addMethod === 'onekey' && <OneKeyOnboard onComplete={refreshConfig} />}

          {addMethod === 'oauth' && (
            <CliSignInPanel providers={providerList} onComplete={refreshConfig} />
          )}

          {addMethod === 'custom' && <CustomEndpointForm onComplete={refreshConfig} />}
          </div>
        </DialogContent>
      </Dialog>

      {/* Provider detail modal — opened by clicking a provider card. Shows the
          provider's models + masked key + endpoint, plus Test / Edit / Delete
          (reusing the existing handlers). */}
      {(() => {
        const prov = detailProviderId ? providers[detailProviderId] : null
        if (!prov) return null
        return (
          <Dialog isOpen={!!prov} onClose={() => setDetailProviderId(null)} title={prov.name} size="xl">
            <DialogContent>
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] uppercase">{prov.protocol}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">{prov.source}</span>
                  <span className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]">{prov.auth_type}</span>
                </div>
                {prov.base_url && (
                  <div>
                    <span className="text-[var(--text-tertiary)]">
                      {t('settings.provider.endpointLabel')}:{' '}
                    </span>
                    <span className="font-mono text-xs break-all">{prov.base_url}</span>
                  </div>
                )}
                <div>
                  <span className="text-[var(--text-tertiary)]">
                    {t('settings.provider.apiKeyLabel')}:{' '}
                  </span>
                  <span className="font-mono text-xs">{prov.api_key_masked || '—'}</span>
                </div>
                {prov.netmind_account_email && (
                  <div>
                    <span className="text-[var(--text-tertiary)]">
                      {t('settings.provider.netmindAccountLabel')}:{' '}
                    </span>
                    <span className="font-mono text-xs break-all">{prov.netmind_account_email}</span>
                  </div>
                )}
                <div>
                  <div className="text-[var(--text-tertiary)] mb-1">{t('settings.provider.modelsCount', { count: prov.models.length })}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {prov.models.map((m) => (
                      <span key={m} className="text-xs px-2 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-secondary)] font-mono">{m}</span>
                    ))}
                  </div>
                </div>
                {testResults[prov.provider_id] && (
                  <p className={cn('text-sm', testResults[prov.provider_id].ok ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]')}>
                    {testResults[prov.provider_id].msg}
                  </p>
                )}
              </div>
            </DialogContent>
            <DialogFooter>
              <button onClick={() => handleTest(prov.provider_id)} disabled={testing === prov.provider_id}
                className="px-4 py-2 text-sm rounded-[var(--radius-lg)] text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/5 disabled:opacity-40 transition-colors">
                {testing === prov.provider_id ? '...' : t('settings.provider.test')}
              </button>
              {/* OAuth cards' model lists are code-owned (codex: curated
                  constant; claude: CLI family aliases) — the backend overrides
                  the stored column at read time, so editing here would be a
                  silent no-op. */}
              {prov.source !== 'claude_oauth' && prov.source !== 'codex_oauth' && (
                <button onClick={() => openEditModels(prov)}
                  className="px-4 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] transition-colors">
                  {t('settings.provider.edit')}
                </button>
              )}
              <button onClick={() => { handleDelete(prov.provider_id); setDetailProviderId(null) }}
                className="px-4 py-2 text-sm rounded-[var(--radius-lg)] text-[var(--color-error)] hover:bg-[var(--color-error)]/5 transition-colors">
                {t('settings.provider.delete')}
              </button>
            </DialogFooter>
          </Dialog>
        )
      })()}

      {/* ================================================================= */}
      {/* Edit-models dialog                                                 */}
      {/* ================================================================= */}
      {(() => {
        const prov = editingProviderId ? providers[editingProviderId] : null
        if (!prov) return null
        return (
          <Dialog
            isOpen={!!prov}
            onClose={editSaving ? () => { /* block close while saving */ } : closeEditModels}
            title={t('settings.provider.editModelsTitle', { name: prov.name })}
            size="2xl"
          >
            <DialogContent>
              <p className="text-sm text-[var(--text-tertiary)] mb-3">
                {t('settings.provider.editModelsHint')}
              </p>
              <ModelBubbleInput
                models={editModels}
                onChange={setEditModels}
                suggestions={MODEL_SUGGESTION_GROUPS}
              />
              {editError && (
                <p className="mt-3 text-sm text-[var(--color-error)]">{editError}</p>
              )}
            </DialogContent>
            <DialogFooter>
              <button
                onClick={closeEditModels}
                disabled={editSaving}
                className="px-4 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-40 transition-colors"
              >
                {t('settings.provider.cancel')}
              </button>
              <button
                onClick={saveEditModels}
                disabled={editSaving}
                className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:bg-[var(--accent-primary)]/90 disabled:opacity-40 transition-colors"
              >
                {editSaving ? t('settings.provider.saving') : t('settings.provider.save')}
              </button>
            </DialogFooter>
          </Dialog>
        )
      })()}

    </div>
  )
}
