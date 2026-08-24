/**
 * @file CliSignInPanel.tsx
 * @description Claude Code Login + Codex CLI Login cards. Extracted from
 * ProviderSettings.tsx's "oauth" tab so the Create Agent wizard's
 * CLI-sign-in step reuses the exact same login/status/setup-token logic
 * Settings already has, instead of a second copy.
 *
 * `providers` only needs source+auth_type — the caller passes whatever
 * subset of its provider wallet it already has in state, this component
 * derives "already added" purely from that.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { isTauri, triggerClaudeLogin, triggerClaudeLogout, cancelClaudeLogin } from '@/lib/tauri'
import { addProvider, fetchClaudeStatus, fetchCodexStatus, type ProviderCliStatus } from './providerApi'

/** How long we let `claude auth login` block before auto-aborting it.
 *  Anthropic's OAuth flow itself has no hard upper bound, but past ~10 min
 *  the user has almost certainly closed the browser tab and the CLI is
 *  just sitting on a dead callback server. Keeping it as a constant so
 *  the value is visible in one place + cheap to tune. */
const CLAUDE_LOGIN_TIMEOUT_SEC = 600

/** "9:32" / "0:08" — countdown formatter for the login timeout label. */
function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${sec.toString().padStart(2, '0')}`
}

/** Best-effort render of whatever expiry value the CLI handed us.
 *
 * The Claude Code CLI shifts schemas across minor versions: some builds
 * emit ISO-8601 strings, others emit unix epoch (sec OR ms). We accept
 * any of them. If parsing fails we just show the raw value rather than
 * eating the field — the user still gets *something* useful. */
function formatExpiresAt(raw: string | null | undefined): string | null {
  if (!raw) return null
  const trimmed = String(raw).trim()
  if (!trimmed) return null
  const n = Number(trimmed)
  let d: Date | null = null
  if (Number.isFinite(n) && n > 0) {
    d = new Date(n < 1e12 ? n * 1000 : n)
  } else {
    const t = Date.parse(trimmed)
    if (!Number.isNaN(t)) d = new Date(t)
  }
  if (!d || Number.isNaN(d.getTime())) return trimmed
  return d.toLocaleString()
}

interface CliSignInPanelProps {
  /** The subset of the caller's provider wallet needed to derive
   *  "already added" — only `source` and `auth_type` are read. */
  providers: Array<{ source: string; auth_type: string }>
  /** Called after any successful addProvider() call (OAuth add or
   *  setup-token save). The caller owns what "complete" means —
   *  refreshing its own provider list, closing a modal, advancing a
   *  wizard step, etc. */
  onComplete: () => void
}

export function CliSignInPanel({ providers, onComplete }: CliSignInPanelProps) {
  const { t } = useTranslation()
  const [claudeStatus, setClaudeStatus] = useState<ProviderCliStatus | null>(null)
  const [claudeLoggingIn, setClaudeLoggingIn] = useState(false)
  const [claudeLoggingOut, setClaudeLoggingOut] = useState(false)
  // Codex CLI Login — parallel to Claude Code Login. Same shape. In
  // local mode the backend auto-installs `@openai/codex` when the
  // user opts into the codex_cli agent framework, but `codex login`
  // (OAuth) is still a manual terminal step because it opens a
  // browser.
  const [codexStatus, setCodexStatus] = useState<ProviderCliStatus | null>(null)
  // Seconds remaining on the login auto-abort timer, or null when no
  // login is in flight. Decremented every 1s by the effect below; on
  // hitting 0 we fire cancelClaudeLogin so the Rust side SIGTERMs the
  // dangling `claude auth login` child.
  const [claudeLoginRemaining, setClaudeLoginRemaining] = useState<number | null>(null)

  // Setup-token paste flow (`claude setup-token` → long-lived subscription
  // token stored server-side, env-injected at spawn; no CLI login state).
  const [setupToken, setSetupToken] = useState('')
  const [savingSetupToken, setSavingSetupToken] = useState(false)

  // Single local error slot shared by the three addProvider() call sites
  // below (Claude OAuth add, setup-token save, Codex OAuth add). Mirrors
  // the pattern CustomEndpointForm.tsx already uses — each add-method
  // component owns its own error state rather than relying on a shared
  // parent-level banner. One slot is fine here because, unlike the old
  // ProviderSettings.tsx tabs, these three actions live in a single panel
  // and a user only ever triggers one at a time.
  const [error, setError] = useState('')

  const claudeCard = providers.find((p) => p.source === 'claude_oauth')
  const hasClaude = claudeCard !== undefined
  // Token transport (`claude setup-token` → CLAUDE_CODE_OAUTH_TOKEN env
  // injection): no CLI login state, no Keychain — see the setup-token
  // section below.
  const claudeTokenConnected = claudeCard?.auth_type === 'oauth_token'
  const hasCodex = providers.some((p) => p.source === 'codex_oauth')

  const refreshStatuses = async () => {
    const [claude, codex] = await Promise.all([fetchClaudeStatus(), fetchCodexStatus()])
    if (claude) setClaudeStatus(claude)
    if (codex) setCodexStatus(codex)
  }

  useEffect(() => {
    // Only ever needs to run once on mount — refreshStatuses itself is
    // re-invoked after every login/logout/add action below.
    refreshStatuses()
  }, [])

  // Login auto-abort timer. Set claudeLoginRemaining to N to start
  // counting down to 0; reaching 0 fires cancelClaudeLogin which
  // SIGTERMs the dangling `claude auth login` child on the Rust side.
  // Setting it to null (e.g. on natural completion) clears the timer.
  useEffect(() => {
    if (claudeLoginRemaining === null) return
    if (claudeLoginRemaining <= 0) {
      cancelClaudeLogin().catch((e) => console.error('cancelClaudeLogin failed:', e))
      // Don't null it here — handleClaudeLogin's finally clears state
      // once the trigger's await resolves with the SIGTERM exit code.
      // Returning early prevents a re-fire next tick.
      return
    }
    const timer = setTimeout(
      () => setClaudeLoginRemaining((r) => (r === null ? null : r - 1)),
      1000,
    )
    return () => clearTimeout(timer)
  }, [claudeLoginRemaining])

  const handleAddClaudeOAuth = async () => {
    const res = await addProvider({ card_type: 'claude_oauth' })
    if (res.ok) {
      setError('')
      onComplete()
    } else {
      setError(res.detail || t('settings.provider.failed'))
    }
  }

  const handleSaveSetupToken = async () => {
    const token = setupToken.trim()
    if (!token) return
    setSavingSetupToken(true)
    try {
      // Same card_type; a non-empty api_key makes the backend store/upgrade
      // the card as auth_type=oauth_token (reconnect-in-place keeps slots).
      const res = await addProvider({ card_type: 'claude_oauth', api_key: token })
      if (res.ok) {
        setError('')
        setSetupToken('')
        onComplete()
      } else {
        setError(res.detail || t('settings.provider.failed'))
      }
    } finally {
      setSavingSetupToken(false)
    }
  }

  const handleAddCodexOAuth = async () => {
    const res = await addProvider({ card_type: 'codex_oauth' })
    if (res.ok) {
      setError('')
      onComplete()
    } else {
      setError(res.detail || t('settings.provider.failed'))
    }
  }

  const handleClaudeLogin = async () => {
    setClaudeLoggingIn(true)
    setClaudeLoginRemaining(CLAUDE_LOGIN_TIMEOUT_SEC)
    try {
      await triggerClaudeLogin()
      // After login completes, refresh to pick up the new status
      await refreshStatuses()
    } catch (e) {
      // SIGTERM from the timeout path also lands here (claude exits
      // non-zero). The finally below resets state regardless.
      console.error('Claude login failed:', e)
    } finally {
      setClaudeLoggingIn(false)
      setClaudeLoginRemaining(null)
    }
  }

  const handleClaudeLogout = async () => {
    setClaudeLoggingOut(true)
    try {
      await triggerClaudeLogout()
      await refreshStatuses()
    } catch (e) {
      console.error('Claude logout failed:', e)
    } finally {
      setClaudeLoggingOut(false)
    }
  }

  return (
    <>
      {/* ---- Claude Code Login Card ----
        *
        * The card surfaces TWO independent pieces of state and lets the
        * user act on each separately:
        *
        *   1. OS credential state — owned by the `claude` CLI and
        *      stored in `~/.claude/.credentials.json`. Drives
        *      Login / Re-login / Logout buttons.
        *
        *   2. Provider record state — owned by NarraNexus and stored
        *      in `user_providers`. Drives the "Add as Provider" /
        *      "Remove" affordance.
        *
        * Earlier versions hid the entire login UI once `hasClaude`
        * was true, which prevented account switching, re-auth after
        * token expiry, and viewing the active account. Decoupling
        * the two layers means a user can re-login, switch accounts,
        * or sign out without first having to delete the provider.
        */}
      <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
        <div className="flex items-center gap-2 mb-1">
          <h4 className="text-sm font-medium text-[var(--text-primary)]">
            {t('settings.provider.claudeLoginTitle')}
          </h4>
        </div>
        <p className="text-sm text-[var(--text-tertiary)] mb-3">{t('settings.provider.claudeOauthDesc')}</p>

        {!claudeStatus && (
          <p className="text-sm text-[var(--text-tertiary)]">{t('settings.provider.checkingStatus')}</p>
        )}

        {claudeStatus && (
          <div className="space-y-3">
            {/* ---- Section A: OS credential state ---- */}
            <div className="space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={cn('inline-block w-2 h-2 rounded-full',
                  claudeStatus.logged_in ? 'bg-[var(--color-success)]' :
                  claudeStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
                )} />
                <span className="text-sm text-[var(--text-secondary)]">
                  {claudeStatus.logged_in
                    ? <>{t('settings.provider.loggedIn')}{claudeStatus.email ? <> {t('settings.provider.loggedInAs')} <span className="font-mono">{claudeStatus.email}</span></> : null}</>
                    : claudeStatus.cli_installed ? t('settings.provider.notLoggedIn') : t('settings.provider.cliNotInstalled')}
                </span>
                {claudeStatus.logged_in && claudeStatus.expires_at && (
                  <span className="text-xs text-[var(--text-tertiary)]">
                    {t('settings.provider.expires', { date: formatExpiresAt(claudeStatus.expires_at) })}
                  </span>
                )}
              </div>

              {/* Action buttons. Always visible when CLI is installed
                * + Tauri — never hidden behind a provider-record check. */}
              {claudeStatus.cli_installed && isTauri() && (
                <div className="flex gap-2 flex-wrap">
                  {claudeStatus.logged_in ? (
                    <>
                      <button onClick={handleClaudeLogin}
                        disabled={claudeLoggingIn || claudeLoggingOut}
                        className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--nm-paper-warm)] disabled:opacity-50 transition-colors">
                        {claudeLoggingIn
                          ? (claudeLoginRemaining !== null
                              ? t('settings.provider.reLoggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                              : t('settings.provider.reLoggingIn'))
                          : t('settings.provider.reLogin')}
                      </button>
                      <button onClick={handleClaudeLogout}
                        disabled={claudeLoggingIn || claudeLoggingOut}
                        className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] border border-[var(--color-error)]/30 text-[var(--color-error)] hover:bg-[var(--color-error)]/5 disabled:opacity-50 transition-colors">
                        {claudeLoggingOut ? t('settings.provider.loggingOut') : t('settings.provider.logout')}
                      </button>
                    </>
                  ) : (
                    <button onClick={handleClaudeLogin}
                      disabled={claudeLoggingIn}
                      className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--accent-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50">
                      {claudeLoggingIn
                        ? (claudeLoginRemaining !== null
                            ? t('settings.provider.loggingInCountdown', { time: formatCountdown(claudeLoginRemaining) })
                            : t('settings.provider.loggingIn'))
                        : t('settings.provider.loginWithClaude')}
                    </button>
                  )}
                </div>
              )}

              {/* Web-mode fallback: no Tauri IPC, user goes to terminal. */}
              {!isTauri() && (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {claudeStatus.cli_installed
                    ? t('settings.provider.webModeInstalled')
                    : t('settings.provider.webModeNotInstalled')}
                </p>
              )}
              {!claudeStatus.cli_installed && isTauri() && (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {t('settings.provider.cliNotInBundle')}
                </p>
              )}
            </div>

            {/* ---- Section B: Provider record state ---- */}
            <div className="pt-2 border-t border-[var(--border-subtle)]">
              {claudeTokenConnected ? (
                <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
                  <span>{'✓'}</span>
                  <span>{t('settings.provider.setupTokenConnected')}</span>
                </div>
              ) : hasClaude ? (
                <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
                  <span>{'✓'}</span>
                  <span>{t('settings.provider.addedAsProvider')}</span>
                </div>
              ) : claudeStatus.logged_in ? (
                <button onClick={handleAddClaudeOAuth}
                  className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors">
                  {t('settings.provider.addAsProvider')}
                </button>
              ) : (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {t('settings.provider.loginToAdd')}
                </p>
              )}
            </div>

            {/* ---- Section C: setup-token connect / replace ----
              *
              * The token transport bypasses the CLI's login state and
              * credential store entirely (the macOS Keychain divergence
              * made staged host credentials unreadable to the runtime
              * CLI — 2026-07-23 incident), so it is the recommended way
              * to connect a subscription. Shown for BOTH states: not
              * connected (recommend) and connected (allow yearly token
              * replacement).
              */}
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
              <p className="text-sm text-[var(--text-tertiary)]">
                {claudeTokenConnected
                  ? t('settings.provider.setupTokenReplaceHint')
                  : t('settings.provider.setupTokenHint')}
              </p>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={setupToken}
                  onChange={(e) => setSetupToken(e.target.value)}
                  placeholder={t('settings.provider.setupTokenPlaceholder')}
                  className="flex-1 px-3 py-2 text-sm rounded-[var(--radius-lg)] border border-[var(--border-default)] bg-[var(--bg-primary)] text-[var(--text-primary)]"
                />
                <button
                  onClick={handleSaveSetupToken}
                  disabled={savingSetupToken || !setupToken.trim()}
                  className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors disabled:opacity-50"
                >
                  {savingSetupToken
                    ? t('settings.provider.setupTokenSaving')
                    : t('settings.provider.setupTokenSave')}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ---- Codex CLI Login Card ----
        *
        * Parallel to "Claude Code Login" above. Same two-layer
        * model:
        *   1. OS credential state — owned by the `codex` CLI and
        *      stored in `~/.codex/auth.json`. Login is a terminal
        *      action (`codex login` opens a browser); we surface
        *      status only — no Tauri IPC for codex yet, so the
        *      card always shows the "run codex login" hint.
        *   2. Provider record state — owned by NarraNexus and
        *      stored in `user_providers`. Drives "Add as Provider"
        *      / "Added ✓" affordance.
        *
        * Once added as a provider, the Codex OAuth credential
        * becomes assignable to the agent slot. The backend
        * auto-installs ``@openai/codex`` when the user picks
        * "Codex CLI" as the Agent Framework, so by the time the
        * user sees this card the binary is usually already on
        * PATH.
        */}
      <div className="p-4 rounded-[var(--radius-xl)] border border-[var(--accent-primary)]/20 bg-[var(--accent-primary)]/5">
        <div className="flex items-center gap-2 mb-1">
          <h4 className="text-sm font-medium text-[var(--text-primary)]">
            {t('settings.provider.codexLoginTitle')}
          </h4>
        </div>
        <p className="text-sm text-[var(--text-tertiary)] mb-3">
          {t('settings.provider.codexOauthDesc')}
        </p>

        {!codexStatus && (
          <p className="text-sm text-[var(--text-tertiary)]">
            {t('settings.provider.checkingStatus')}
          </p>
        )}

        {codexStatus && (
          <div className="space-y-3">
            {/* ---- Section A: OS credential state ---- */}
            <div className="space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={cn('inline-block w-2 h-2 rounded-full',
                  codexStatus.logged_in ? 'bg-[var(--color-success)]' :
                  codexStatus.cli_installed ? 'bg-[var(--color-warning)]' : 'bg-[var(--text-tertiary)]'
                )} />
                <span className="text-sm text-[var(--text-secondary)]">
                  {codexStatus.logged_in
                    ? <>{t('settings.provider.loggedIn')}{codexStatus.email ? <> {t('settings.provider.loggedInAs')} <span className="font-mono">{codexStatus.email}</span></> : null}</>
                    : codexStatus.cli_installed
                      ? t('settings.provider.notLoggedIn')
                      : t('settings.provider.cliNotInstalled')}
                </span>
                {codexStatus.logged_in && codexStatus.expires_at && (
                  <span className="text-xs text-[var(--text-tertiary)]">
                    {t('settings.provider.expires', { date: formatExpiresAt(codexStatus.expires_at) })}
                  </span>
                )}
              </div>

              {/* Always show terminal hint. Codex CLI's OAuth flow
                * opens a browser when `codex login` runs; we don't
                * shell out via Tauri yet (unlike claude). */}
              <p className="text-sm text-[var(--text-tertiary)]">
                {codexStatus.cli_installed
                  ? t('settings.provider.codexTerminalHint')
                  : t('settings.provider.codexInstallHint')}
              </p>
            </div>

            {/* ---- Section B: Provider record state ---- */}
            <div className="pt-2 border-t border-[var(--border-subtle)]">
              {hasCodex ? (
                <div className="flex items-center gap-2 text-sm text-[var(--color-success)]">
                  <span>{'✓'}</span>
                  <span>{t('settings.provider.codexAddedAsProvider')}</span>
                </div>
              ) : codexStatus.logged_in ? (
                <button onClick={handleAddCodexOAuth}
                  className="px-4 py-2 text-sm font-medium rounded-[var(--radius-lg)] bg-[var(--text-primary)] text-[var(--text-inverse)] hover:opacity-90 transition-colors">
                  {t('settings.provider.addAsProvider')}
                </button>
              ) : (
                <p className="text-sm text-[var(--text-tertiary)]">
                  {t('settings.provider.codexLoginToAdd')}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
    </>
  )
}
