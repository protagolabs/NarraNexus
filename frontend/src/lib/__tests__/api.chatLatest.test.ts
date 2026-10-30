/**
 * api.getChatLatest — the wire contract with GET /api/agents/chat-latest: one
 * `agent_ids` query parameter per id. The backend reads a list parameter; a
 * comma-joined value would arrive as ONE id, answer 200 with an empty result,
 * and silently switch every background reply notice off.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

describe('api.getChatLatest', () => {
  test('sends one agent_ids parameter per id', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ success: true, latest: {} }), { status: 200 }),
    );
    await api.getChatLatest(['agent_a', 'agent_b']);
    const url = new URL(String(fetchSpy.mock.calls[0][0]), 'http://x.invalid');
    expect(url.pathname).toBe('/api/agents/chat-latest');
    expect(url.searchParams.getAll('agent_ids')).toEqual(['agent_a', 'agent_b']);
  });
});
