/**
 * api.deleteFile — a file name is one path segment, whatever it contains.
 *
 * With encodeURI a `?` or `#` in a name was left alone, so the URL's path stopped
 * there and the request deleted a prefix of the path instead: `dir/a#b` deleted
 * `dir/a`. Names come from the workspace listing, so an agent can plant one.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '../api';

afterEach(() => vi.restoreAllMocks());

async function deletedUrl(path: string): Promise<URL> {
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ success: true }),
  } as unknown as Response);
  await api.deleteFile('agent_1', path);
  return new URL(String(fetchSpy.mock.calls[0][0]), 'http://localhost');
}

describe('api.deleteFile', () => {
  test.each([
    ['report#1.txt', '/report%231.txt'],
    ['a?b=c.txt', '/a%3Fb%3Dc.txt'],
    ['dir/x#y/z?.txt', '/dir/x%23y/z%3F.txt'],
    ['100%.txt', '/100%25.txt'],
    ['a\\..\\b', '/a%5C..%5Cb'],
  ])('%s stays inside the path', async (path, suffix) => {
    const url = await deletedUrl(path);
    expect(url.pathname).toBe(`/api/agents/agent_1/files${suffix}`);
    expect(url.search).toBe('');
    expect(url.hash).toBe('');
  });

  test('a nested path keeps its slashes', async () => {
    const url = await deletedUrl('docs/sub dir/note.md');
    expect(url.pathname).toBe('/api/agents/agent_1/files/docs/sub%20dir/note.md');
  });
});
