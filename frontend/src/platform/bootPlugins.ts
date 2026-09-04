/**
 * @file_name: bootPlugins.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: App-level wiring of the plugin loader: error reporting to the factory API, then `loadPlugins()`.
 */
import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { setErrorPoster } from './errorSink';
import { loadPlugins } from './loader';

export async function bootPlugins(): Promise<void> {
  setErrorPoster((pluginId, body) =>
    fetch(`${getApiBaseUrl()}/api/plugin-factory/${encodeURIComponent(pluginId)}/errors`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(body),
    }),
  );
  await loadPlugins();
}
