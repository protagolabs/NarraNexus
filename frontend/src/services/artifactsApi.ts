/**
 * Artifacts REST API client — thin fetch wrappers for /api/agents/{agentId}/artifacts
 */

import type { Artifact, ArtifactWithVersions } from '@/types/artifact';

const base = (agentId: string) => `/api/agents/${agentId}/artifacts`;

export const artifactsApi = {
  async listSession(agentId: string, sessionId: string): Promise<Artifact[]> {
    const url = `${base(agentId)}?scope=session&session_id=${encodeURIComponent(sessionId)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`listSession failed: ${r.status}`);
    return r.json();
  },

  async listPinned(agentId: string): Promise<Artifact[]> {
    const r = await fetch(`${base(agentId)}?scope=pinned`);
    if (!r.ok) throw new Error(`listPinned failed: ${r.status}`);
    return r.json();
  },

  async getDetail(agentId: string, artifactId: string): Promise<ArtifactWithVersions> {
    const r = await fetch(`${base(agentId)}/${artifactId}`);
    if (!r.ok) throw new Error(`getDetail failed: ${r.status}`);
    return r.json();
  },

  async setPinned(agentId: string, artifactId: string, pinned: boolean): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pinned }),
    });
    if (!r.ok) throw new Error(`setPinned failed: ${r.status}`);
    return r.json();
  },

  async remove(agentId: string, artifactId: string): Promise<void> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`delete failed: ${r.status}`);
  },
};
