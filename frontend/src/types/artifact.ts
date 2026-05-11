/**
 * Artifact type definitions — mirrors backend ArtifactSchema / ArtifactVersionSchema
 */

export type ArtifactKind =
  | 'text/html'
  | 'application/vnd.echarts+json'
  | 'text/csv'
  | 'text/markdown'
  | 'image/png'
  | 'image/jpeg'
  | 'application/pdf';

export interface ArtifactVersion {
  id: number;
  artifact_id: string;
  version: number;
  file_path: string;
  size_bytes: number;
  created_at: string;
}

export interface Artifact {
  artifact_id: string;
  agent_id: string;
  user_id: string;
  session_id: string | null;
  title: string;
  kind: ArtifactKind;
  description: string | null;
  pinned: boolean;
  latest_version: number;
  created_at: string;
  updated_at: string;
}

export interface ArtifactWithVersions {
  artifact: Artifact;
  versions: ArtifactVersion[];
}

export function rawUrl(agentId: string, artifactId: string, version: number): string {
  return `/api/agents/${agentId}/artifacts/${artifactId}/v${version}/raw`;
}
