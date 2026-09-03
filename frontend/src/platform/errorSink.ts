/**
 * @file_name: errorSink.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Where UI crashes are reported, attributed to the code that owns them.
 *
 * Until now a render crash only reached `console.error`. The sink keeps the
 * console output and adds subscribers (batch 2 wires the plugin factory's
 * health view and a backend report) plus attribution. Attribution is a
 * best-effort heuristic: the plugin loader records each plugin's chunk URL
 * prefix, and a crash whose `error.stack` text contains that prefix is
 * blamed on the plugin; anything else is "shell". Stacks are not
 * normalised across browsers, so this can under-attribute (never
 * mis-attribute to another plugin unless two prefixes overlap).
 */

export interface UiErrorReport {
  source: string; // "shell" or a plugin id
  error: Error;
  componentStack?: string;
  kind: 'render' | 'chunk';
  at: number;
}

type Listener = (report: UiErrorReport) => void;

const listeners = new Set<Listener>();
const recent: UiErrorReport[] = [];
const RECENT_LIMIT = 50;
const pluginUrls = new Map<string, string>(); // chunk URL prefix -> plugin id

export function attributeChunkUrl(urlPrefix: string, pluginId: string): void {
  pluginUrls.set(urlPrefix, pluginId);
}

export function sourceFor(error: Error): string {
  const stack = error.stack ?? '';
  for (const [prefix, pluginId] of pluginUrls) {
    if (stack.includes(prefix)) return pluginId;
  }
  return 'shell';
}

export function reportUiError(error: Error, opts: { componentStack?: string; kind?: 'render' | 'chunk' } = {}): UiErrorReport {
  const report: UiErrorReport = {
    source: sourceFor(error),
    error,
    componentStack: opts.componentStack,
    kind: opts.kind ?? 'render',
    at: Date.now(),
  };
  recent.push(report);
  if (recent.length > RECENT_LIMIT) recent.shift();
  for (const l of listeners) {
    try {
      l(report);
    } catch {
      // a broken listener must not mask the original error
    }
  }
  return report;
}

export function onUiError(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function recentUiErrors(): readonly UiErrorReport[] {
  return recent;
}

/** Test hook: clear attribution and history. */
export function resetErrorSink(): void {
  listeners.clear();
  recent.length = 0;
  pluginUrls.clear();
}
