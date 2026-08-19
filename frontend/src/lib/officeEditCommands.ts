/**
 * @file_name: officeEditCommands.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: The T1 office direct-edit vocabulary (spec B §3.3) — builders
 * for officecli watch `/api/batch` items and the parser for the watch page's
 * selection reports. Verified against officecli 1.0.144 (2026-08-19 probe):
 * batch items are {command, path, props?}; `set` carries changes in `props`
 * (text / bold / italic / color); selections arrive as {"paths": [...]}.
 * Every surface that talks to the watch edit API goes through this module so
 * the wire format lives in exactly one place.
 */

export interface WatchCommand {
  command: 'remove' | 'set';
  path: string;
  props?: Record<string, string | boolean>;
}

export function buildRemoveCommands(paths: string[]): WatchCommand[] {
  return paths.map((path) => ({ command: 'remove', path }));
}

export function buildSetPropsCommands(
  paths: string[],
  props: Record<string, string | boolean>,
): WatchCommand[] {
  return paths.map((path) => ({ command: 'set', path, props }));
}

export function buildSetTextCommand(path: string, text: string): WatchCommand {
  return { command: 'set', path, props: { text } };
}

/** Parse the selection body the watch page POSTs to /api/selection. */
export function parseSelectionMessage(body: string): string[] {
  try {
    const parsed = JSON.parse(body);
    const paths = Array.isArray(parsed) ? parsed : parsed?.paths;
    if (!Array.isArray(paths)) return [];
    return paths.filter((p): p is string => typeof p === 'string');
  } catch {
    return [];
  }
}
