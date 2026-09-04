/**
 * @file_name: PluginFactory.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Settings → Plugins → "User plugins": the factory page over /api/plugin-factory.
 *
 * Installed user plugins with their kernel state, warnings and isolation
 * reason; install from a GitHub `owner/repo[@tag]`, `owner/repo#ref`, or a
 * local directory; a permissions disclosure the user must acknowledge
 * before a third-party plugin is enabled (spec §10.4); enable / disable /
 * upgrade / uninstall; last-known-good rollback; the safe-mode banner with
 * the bisect wizard (spec §9.5); and the per-plugin error log fed by the
 * frontend error sink. Every mutation ends with a "restart required" note —
 * plugins load at boot, and the page never pretends otherwise. Hidden in
 * cloud mode (plugins are baked into the image there).
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Download, Power, PowerOff, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';

import { api } from '@/lib/api';
import { Button, PaperCard, StatusBadge, TextInput } from '@/components/nm';
import type { FactoryListResponse, FactoryPlugin, FactoryProposal } from '@/types';

type Data = NonNullable<FactoryListResponse['data']>;

const STATE_TONE: Record<string, 'success' | 'warning' | 'error' | 'info' | 'neutral'> = {
  active: 'success',
  enabled: 'success',
  validated: 'info',
  registered: 'info',
  slow: 'warning',
  deps_missing: 'warning',
  disabled: 'neutral',
  crashed: 'error',
  blocked: 'error',
  incompatible: 'error',
  missing: 'error',
};

export function PluginFactory() {
  const { t } = useTranslation();
  const [data, setData] = useState<Data | null>(null);
  const [loadError, setLoadError] = useState('');
  const [source, setSource] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [errorText, setErrorText] = useState('');
  const [openErrors, setOpenErrors] = useState<Record<string, { at: number; kind: string; message: string }[] | undefined>>({});
  const [pendingAck, setPendingAck] = useState<FactoryPlugin | null>(null);
  const [proposals, setProposals] = useState<FactoryProposal[]>([]);

  const load = useCallback(async () => {
    try {
      const res = await api.factoryList();
      if (res.success && res.data) {
        setData(res.data);
        setLoadError('');
      } else {
        setLoadError(res.error || t('pages.settings.plugins.factory.loadFailed'));
      }
      if (!res.data?.cloud_managed) {
        // Agent-originated requests waiting for the user (self-extension, spec §11.5).
        const props = await api.factoryProposals().catch(() => null);
        setProposals(props?.data?.proposals ?? []);
      }
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('pages.settings.plugins.factory.loadFailed'));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key);
    setErrorText('');
    try {
      await fn();
      await load();
      if (done) setNotice(done);
    } catch (e) {
      setErrorText(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const install = () =>
    run(
      'install',
      async () => {
        const res = await api.factoryInstall(source.trim());
        if (!res.success || !res.data) throw new Error(res.error || t('pages.settings.plugins.factory.loadFailed'));
        setSource('');
        const perms = res.data.permissions ?? {};
        const declares = Boolean(perms.network?.length || perms.filesystem?.length || perms.subprocess || perms.env?.length);
        if (declares) {
          setPendingAck({
            id: res.data.id,
            display_name: res.data.id,
            permissions: perms,
          } as FactoryPlugin);
        }
      },
      t('pages.settings.plugins.factory.restartRequired'),
    );

  const toggleErrors = async (id: string) => {
    if (openErrors[id]) {
      setOpenErrors((s) => ({ ...s, [id]: undefined }));
      return;
    }
    const res = await api.factoryErrors(id);
    setOpenErrors((s) => ({ ...s, [id]: res.data?.errors ?? [] }));
  };

  if (loadError) return <p className="text-sm text-[var(--color-error)]">{loadError}</p>;
  if (!data) return <p className="text-sm text-[var(--text-tertiary)]">{t('pages.settings.plugins.loading')}</p>;
  if (data.cloud_managed) return null;

  const bisect = data.bisect;
  const fp = 'pages.settings.plugins.factory';

  return (
    <div className="space-y-4" data-testid="plugin-factory">
      <div>
        <div className="text-sm font-medium text-[var(--nm-ink)]">{t(`${fp}.title`)}</div>
        <div className="text-xs text-[var(--nm-ink50)] mt-0.5">{t(`${fp}.hint`)}</div>
      </div>

      {data.safe_mode && (
        <PaperCard padding="md" className="space-y-2 border border-[var(--color-warning)]" data-testid="safe-mode-banner">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--nm-ink)]">
            <AlertTriangle className="h-4 w-4 text-[var(--color-warning)]" />
            {t(`${fp}.safeModeTitle`)}
          </div>
          <p className="text-xs text-[var(--nm-ink50)]">{data.safe_mode_reason}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => void run('safe', () => api.factoryLeaveSafeMode(), t(`${fp}.restartRequired`))}>
              {t(`${fp}.safeModeLeave`)}
            </Button>
            {!bisect && (
              <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void run('bisect', () => api.factoryBisect('start'))}>
                {t(`${fp}.bisectStart`)}
              </Button>
            )}
          </div>
        </PaperCard>
      )}

      {bisect && (
        <PaperCard padding="md" className="space-y-2" data-testid="bisect-wizard">
          <div className="text-sm font-medium text-[var(--nm-ink)]">
            {t(`${fp}.bisectTrial`, { count: bisect.candidates.length })}
          </div>
          <p className="text-xs font-mono text-[var(--nm-ink50)]">{bisect.trial.join(', ') || '—'}</p>
          {bisect.candidates.length === 1 && (
            <p className="text-xs text-[var(--color-error)]">{t(`${fp}.bisectCulprit`, { id: bisect.candidates[0] })}</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {bisect.candidates.length > 1 && (
              <>
                <Button size="sm" disabled={busy !== null} onClick={() => void run('bisect', () => api.factoryBisectAnswer(true))}>
                  {t(`${fp}.bisectGood`)}
                </Button>
                <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => void run('bisect', () => api.factoryBisectAnswer(false))}>
                  {t(`${fp}.bisectBad`)}
                </Button>
              </>
            )}
            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void run('bisect', () => api.factoryBisect('stop'), t(`${fp}.restartRequired`))}>
              {t(`${fp}.bisectStop`)}
            </Button>
          </div>
        </PaperCard>
      )}

      <PaperCard padding="md" className="space-y-2">
        <div className="flex flex-col sm:flex-row gap-2">
          <TextInput
            aria-label={t(`${fp}.sourcePlaceholder`)}
            placeholder={t(`${fp}.sourcePlaceholder`)}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="flex-1"
          />
          <Button size="sm" leading={<Download className="h-3.5 w-3.5" />} disabled={busy !== null || !source.trim()} loading={busy === 'install'} onClick={() => void install()}>
            {busy === 'install' ? t(`${fp}.installing`) : t(`${fp}.install`)}
          </Button>
        </div>
        <p className="text-[11px] text-[var(--nm-ink50)]">{t(`${fp}.sourceHelp`)}</p>
        {errorText && <p className="text-xs text-[var(--color-error)]" role="alert">{errorText}</p>}
        {notice && <p className="text-xs text-[var(--color-warning)]" role="status">{notice}</p>}
      </PaperCard>

      {proposals.length > 0 && (
        <div className="space-y-2" data-testid="proposals">
          <div className="text-sm font-medium text-[var(--nm-ink)]">{t(`${fp}.proposalsTitle`)}</div>
          {proposals.map((p) => (
            <PaperCard key={p.id} padding="md" className="space-y-2 border border-[var(--color-info)]" data-testid={`proposal-${p.id}`}>
              <div className="text-sm text-[var(--nm-ink)]">{p.summary}</div>
              <div className="text-xs font-mono text-[var(--nm-ink50)]">{p.plugin_id} · {p.action} · {p.scope} · {t(`${fp}.proposalBy`, { agent: p.agent_id })}</div>
              {p.test_report?.ok !== undefined && (
                <div className="text-xs text-[var(--nm-ink70)]">
                  {p.test_report.ok ? t(`${fp}.proposalTestsGreen`, { count: p.test_report.passed ?? 0 }) : t(`${fp}.proposalTestsRed`)}
                </div>
              )}
              <ul className="text-xs text-[var(--nm-ink70)] list-disc pl-4 space-y-0.5">
                {p.permissions?.network?.length ? <li>{t(`${fp}.permissionsNetwork`)}: {p.permissions.network.join(', ')}</li> : null}
                {p.permissions?.filesystem?.length ? <li>{t(`${fp}.permissionsFilesystem`)}: {p.permissions.filesystem.join(', ')}</li> : null}
                {p.permissions?.subprocess ? <li>{t(`${fp}.permissionsSubprocess`)}</li> : null}
                {p.permissions?.env?.length ? <li>{t(`${fp}.permissionsEnv`)}: {p.permissions.env.join(', ')}</li> : null}
                {!p.permissions?.network?.length && !p.permissions?.filesystem?.length && !p.permissions?.subprocess && !p.permissions?.env?.length ? <li>{t(`${fp}.noPermissions`)}</li> : null}
              </ul>
              <div className="flex items-center gap-2">
                <Button size="sm" disabled={busy !== null} onClick={() => void run(p.id, () => api.factoryDecide(p.id, true), t(`${fp}.restartRequired`))}>
                  {t(`${fp}.proposalApprove`)}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void run(p.id, () => api.factoryDecide(p.id, false))}>
                  {t(`${fp}.proposalReject`)}
                </Button>
              </div>
            </PaperCard>
          ))}
        </div>
      )}

      {pendingAck && (
        <PaperCard padding="md" className="space-y-2 border border-[var(--color-warning)]" data-testid="permissions-dialog" role="dialog">
          <div className="text-sm font-medium text-[var(--nm-ink)]">{t(`${fp}.permissionsTitle`, { id: pendingAck.id })}</div>
          <ul className="text-xs text-[var(--nm-ink70)] list-disc pl-4 space-y-0.5">
            {pendingAck.permissions.network?.length ? <li>{t(`${fp}.permissionsNetwork`)}: {pendingAck.permissions.network.join(', ')}</li> : null}
            {pendingAck.permissions.filesystem?.length ? <li>{t(`${fp}.permissionsFilesystem`)}: {pendingAck.permissions.filesystem.join(', ')}</li> : null}
            {pendingAck.permissions.subprocess ? <li>{t(`${fp}.permissionsSubprocess`)}</li> : null}
            {pendingAck.permissions.env?.length ? <li>{t(`${fp}.permissionsEnv`)}: {pendingAck.permissions.env.join(', ')}</li> : null}
          </ul>
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={busy !== null} onClick={() => void run('ack', () => api.factoryAction(pendingAck.id, 'acknowledge-permissions')).then(() => setPendingAck(null))}>
              {t(`${fp}.permissionsAck`)}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void run('ack', () => api.factoryAction(pendingAck.id, 'disable')).then(() => setPendingAck(null))}>
              {t(`${fp}.permissionsKeepDisabled`)}
            </Button>
          </div>
        </PaperCard>
      )}

      {data.plugins.length === 0 && <p className="text-sm text-[var(--nm-ink50)]">{t(`${fp}.empty`)}</p>}

      {data.plugins.map((p) => {
        const isBusy = busy === p.id;
        const errs = openErrors[p.id];
        return (
          <PaperCard key={p.id} padding="md" className="space-y-2" data-testid={`factory-plugin-${p.id}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-[var(--nm-ink)]">
                  {p.display_name} <span className="text-xs text-[var(--nm-ink50)]">v{p.version}</span>
                </div>
                <div className="text-xs text-[var(--nm-ink50)] mt-0.5 font-mono">{p.id} · {t(`${fp}.mode.${p.mode}`)} · {p.scope}</div>
                {p.description && <div className="text-xs text-[var(--nm-ink70)] mt-1">{p.description}</div>}
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={STATE_TONE[p.state] ?? 'neutral'}>{t(`${fp}.state.${p.state}`)}</StatusBadge>
                {!p.enabled && <StatusBadge status="neutral">{t(`${fp}.disabled`)}</StatusBadge>}
              </div>
            </div>
            {p.isolated && <p className="text-xs text-[var(--color-error)]">{t(`${fp}.isolated`)}: {p.isolated}</p>}
            {p.last_error && !p.isolated && <p className="text-xs text-[var(--color-error)]">{p.last_error}</p>}
            {p.warnings.length > 0 && (
              <ul className="text-xs text-[var(--color-warning)] list-disc pl-4">
                {p.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
            {p.provides.length > 0 && <p className="text-[11px] font-mono text-[var(--nm-ink50)]">{p.provides.join(' · ')}</p>}
            <div className="flex flex-wrap items-center gap-2">
              {p.enabled ? (
                <Button size="sm" variant="secondary" leading={<PowerOff className="h-3.5 w-3.5" />} disabled={isBusy || p.protected} onClick={() => void run(p.id, () => api.factoryAction(p.id, 'disable'), t(`${fp}.restartRequired`))}>
                  {t(`${fp}.disable`)}
                </Button>
              ) : (
                <Button size="sm" leading={<Power className="h-3.5 w-3.5" />} disabled={isBusy} onClick={() => void run(p.id, () => api.factoryAction(p.id, 'enable'), t(`${fp}.restartRequired`))}>
                  {t(`${fp}.enable`)}
                </Button>
              )}
              {p.source.type !== 'local' && (
                <Button size="sm" variant="ghost" leading={<RefreshCw className="h-3.5 w-3.5" />} disabled={isBusy} onClick={() => void run(p.id, () => api.factoryAction(p.id, 'upgrade'), t(`${fp}.restartRequired`))}>
                  {t(`${fp}.upgrade`)}
                </Button>
              )}
              {!p.protected && (
                <Button size="sm" variant="ghost" leading={<Trash2 className="h-3.5 w-3.5" />} disabled={isBusy} onClick={() => void run(p.id, () => api.factoryAction(p.id, 'uninstall'), t(`${fp}.restartRequired`))}>
                  {t(`${fp}.uninstall`)}
                </Button>
              )}
              <Button size="sm" variant="ghost" disabled={isBusy} onClick={() => void toggleErrors(p.id)}>
                {errs ? t(`${fp}.hideErrors`) : t(`${fp}.viewErrors`, { count: p.recent_errors })}
              </Button>
            </div>
            {errs && (
              <div className="rounded-[var(--radius-sm)] bg-[var(--nm-paper-warm)] p-2 text-[10px] font-mono text-[var(--nm-ink50)] max-h-32 overflow-y-auto space-y-0.5" data-testid={`factory-errors-${p.id}`}>
                {errs.length === 0 ? <div>{t(`${fp}.noErrors`)}</div> : errs.map((e, i) => <div key={i}>[{e.kind}] {e.message}</div>)}
              </div>
            )}
          </PaperCard>
        );
      })}

      {data.plugins.length > 0 && (
        <div>
          <Button size="sm" variant="ghost" leading={<RotateCcw className="h-3.5 w-3.5" />} disabled={busy !== null} onClick={() => void run('rollback', () => api.factoryRollback(), t(`${fp}.restartRequired`))}>
            {t(`${fp}.rollback`)}
          </Button>
        </div>
      )}
    </div>
  );
}
