/**
 * @file_name: ServiceCard.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Service status card for the System page
 *
 * Displays a single service's status with indicator dot, port info,
 * and optional restart button.
 *
 * 2026-07-22: optional expandable per-worker detail. The consolidated
 * `workers` supervisor runs four sub-workers (poller / jobs / bus / channels)
 * in one process, so the process-level dot can read "running" while a
 * sub-worker crash-loops. When `workers` is passed, the card shows a flap
 * warning (any sub-worker restarting / restartCount>0) and an expandable list
 * of each sub-worker's state + cumulative restart count.
 */

import { useState } from 'react';
import { RotateCw, ChevronRight, ChevronDown, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent, Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { WorkerLiveness, WorkerState } from '@/types/platform';

type ServiceStatus =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'crashed'
  | 'healthy'
  | 'unhealthy'
  | 'unknown';

interface ServiceCardProps {
  label: string;
  status: ServiceStatus;
  port: number | null;
  lastError: string | null;
  onRestart?: () => void;
  /** Per-worker liveness for the consolidated `workers` service (optional). */
  workers?: WorkerLiveness[];
}

const STATUS_CONFIG: Record<
  ServiceStatus,
  { color: string; bg: string; pulse: boolean }
> = {
  healthy: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  running: {
    color: 'bg-[var(--color-success)]',
    bg: 'shadow-[0_0_8px_var(--color-success)]',
    pulse: true,
  },
  starting: {
    color: 'bg-[var(--color-warning)]',
    bg: 'shadow-[0_0_8px_var(--color-warning)]',
    pulse: true,
  },
  crashed: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  unhealthy: {
    color: 'bg-[var(--color-error)]',
    bg: 'shadow-[0_0_8px_var(--color-error)]',
    pulse: false,
  },
  stopped: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
  unknown: {
    color: 'bg-[var(--text-tertiary)]',
    bg: '',
    pulse: false,
  },
};

/** Dot color per sub-worker state (subset of the service palette). */
const WORKER_DOT: Record<WorkerState, string> = {
  running: 'bg-[var(--color-success)]',
  starting: 'bg-[var(--color-warning)]',
  restarting: 'bg-[var(--color-error)]',
  stopped: 'bg-[var(--text-tertiary)]',
  unknown: 'bg-[var(--text-tertiary)]',
};

export function ServiceCard({
  label,
  status,
  port,
  lastError,
  onRestart,
  workers,
}: ServiceCardProps) {
  const { t } = useTranslation();
  const config = STATUS_CONFIG[status];
  const [expanded, setExpanded] = useState(false);

  const hasWorkers = !!workers && workers.length > 0;
  // A sub-worker that is restarting, or has ever restarted, means the process
  // dot ("running") is hiding a problem — surface it on the header.
  const flapping =
    hasWorkers &&
    workers!.some((w) => w.state === 'restarting' || w.restartCount > 0);

  return (
    <Card variant="default">
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            {/* Header row: indicator + label */}
            <div className="flex items-center gap-2.5 mb-2">
              <span className="relative flex h-3 w-3">
                {config.pulse && (
                  <span
                    className={cn(
                      'absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping',
                      config.color,
                    )}
                  />
                )}
                <span
                  className={cn(
                    'relative inline-flex rounded-full h-3 w-3',
                    config.color,
                    config.bg,
                  )}
                />
              </span>
              <span className="text-sm font-semibold text-[var(--text-primary)] truncate">
                {label}
              </span>
              {flapping && (
                <span
                  className="flex items-center gap-1 text-[var(--color-warning)]"
                  title={t('system.serviceCard.workersFlapping')}
                >
                  <AlertTriangle className="w-3.5 h-3.5" />
                </span>
              )}
            </div>

            {/* Status + port */}
            <div className="flex items-center gap-3 text-xs text-[var(--text-secondary)]">
              <span>{t(`system.serviceStatus.${status}`)}</span>
              {port != null && (
                <span className="font-mono text-[var(--text-tertiary)]">
                  :{port}
                </span>
              )}
            </div>

            {/* Error message */}
            {lastError && (
              <p className="mt-2 text-xs text-[var(--color-error)] truncate">
                {lastError}
              </p>
            )}
          </div>

          {/* Restart button */}
          {onRestart && (
            <Button
              variant="ghost"
              size="icon"
              onClick={onRestart}
              title={t('system.serviceCard.restart')}
              className="shrink-0"
            >
              <RotateCw className="w-4 h-4" />
            </Button>
          )}
        </div>

        {/* Expandable per-worker detail (only for the consolidated `workers`) */}
        {hasWorkers && (
          <div className="border-t border-[var(--border-subtle)] pt-1.5">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            >
              {expanded ? (
                <ChevronDown className="w-3.5 h-3.5" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5" />
              )}
              <span>
                {t('system.serviceCard.workerCount', { count: workers!.length })}
              </span>
            </button>

            {expanded && (
              <ul className="mt-1.5 flex flex-col gap-1">
                {workers!.map((w) => (
                  <li
                    key={w.name}
                    className="flex items-center gap-2 text-xs"
                    title={w.lastError ?? undefined}
                  >
                    <span
                      className={cn(
                        'inline-flex rounded-full h-2 w-2 shrink-0',
                        WORKER_DOT[w.state] ?? WORKER_DOT.unknown,
                      )}
                    />
                    <span className="text-[var(--text-primary)] font-mono">
                      {w.name}
                    </span>
                    <span className="text-[var(--text-tertiary)]">
                      {t(`system.workerState.${w.state}`)}
                    </span>
                    {w.restartCount > 0 && (
                      <span className="text-[var(--color-warning)]">
                        {t('system.serviceCard.restarts', {
                          count: w.restartCount,
                        })}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export type { ServiceCardProps, ServiceStatus };
