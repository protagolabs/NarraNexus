/**
 * Artifacts REST API client — thin fetch wrappers for the agent-scoped
 * (/api/agents/{agentId}/artifacts) and user-scoped
 * (/api/users/{userId}/artifacts) routes.
 *
 * Cloud mode (where the backend enforces JWT-based agent / user ownership)
 * needs every request to carry an `Authorization: Bearer <jwt>` header.
 * The auth helper below mirrors `frontend/src/lib/api.ts` so we read the
 * same JWT slot the rest of the app relies on without forcing every
 * artifact call to go through ApiClient.
 */

import type { Artifact, ArtifactWithVersions } from '@/types/artifact';

const base = (agentId: string) => `/api/agents/${agentId}/artifacts`;
const userBase = (userId: string) => `/api/users/${userId}/artifacts`;

function authHeaders(): Record<string, string> {
  try {
    const raw = localStorage.getItem('narra-nexus-config');
    if (raw) {
      const cfg = JSON.parse(raw);
      const token = cfg?.state?.token;
      if (token) return { Authorization: `Bearer ${token}` };
    }
  } catch {
    /* localStorage may be unavailable / disabled — fall through */
  }
  return {};
}

export interface ArtifactQuotaInfo {
  used_count: number;
  count_limit: number;
  used_bytes: number;
  bytes_limit: number;
  is_cloud_mode: boolean;
}

export interface BulkDeleteResult {
  deleted: number;
  skipped_not_owned: string[];
}

export const artifactsApi = {
  async listSession(agentId: string, sessionId: string): Promise<Artifact[]> {
    const url = `${base(agentId)}?scope=session&session_id=${encodeURIComponent(sessionId)}`;
    const r = await fetch(url, { headers: authHeaders() });
    if (!r.ok) throw new Error(`listSession failed: ${r.status}`);
    return r.json();
  },

  async listPinned(agentId: string): Promise<Artifact[]> {
    const r = await fetch(`${base(agentId)}?scope=pinned`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`listPinned failed: ${r.status}`);
    return r.json();
  },

  async getDetail(agentId: string, artifactId: string): Promise<ArtifactWithVersions> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`getDetail failed: ${r.status}`);
    return r.json();
  },

  async setPinned(agentId: string, artifactId: string, pinned: boolean): Promise<Artifact> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ pinned }),
    });
    if (!r.ok) throw new Error(`setPinned failed: ${r.status}`);
    return r.json();
  },

  async remove(agentId: string, artifactId: string): Promise<void> {
    const r = await fetch(`${base(agentId)}/${artifactId}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error(`delete failed: ${r.status}`);
  },

  // ── user-scoped (Settings → Artifacts management UI) ────────────────────────

  async listAll(userId: string): Promise<Artifact[]> {
    const r = await fetch(userBase(userId), { headers: authHeaders() });
    if (!r.ok) throw new Error(`listAll failed: ${r.status}`);
    return r.json();
  },

  async getQuota(userId: string): Promise<ArtifactQuotaInfo> {
    const r = await fetch(`${userBase(userId)}/quota`, { headers: authHeaders() });
    if (!r.ok) throw new Error(`getQuota failed: ${r.status}`);
    return r.json();
  },

  async bulkDelete(userId: string, artifactIds: string[]): Promise<BulkDeleteResult> {
    const r = await fetch(userBase(userId), {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ artifact_ids: artifactIds }),
    });
    if (!r.ok) throw new Error(`bulkDelete failed: ${r.status}`);
    return r.json();
  },
};
