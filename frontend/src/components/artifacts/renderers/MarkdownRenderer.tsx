/**
 * @file_name: MarkdownRenderer.tsx
 * @description: Lazy-loaded renderer for text/markdown artifacts.
 *
 * Fetches raw Markdown text and renders it via ReactMarkdown + remark-gfm.
 * Uses the same vendor-markdown chunk that MessageBubble / ui/Markdown.tsx
 * already relies on, so no additional bundle cost when Markdown is used
 * elsewhere in the session.
 *
 * Note: unlike ui/Markdown.tsx this component owns its own fetch because the
 * content comes from an artifact URL rather than being passed as a prop string.
 * Both components use the same ReactMarkdown + remark-gfm pipeline under the hood.
 */

import { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';
import { fetchArtifactText } from '@/services/artifactsApi';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function MarkdownRenderer({ artifact, version }: Props) {
  const [text, setText] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchArtifactText(rawUrl(artifact.agent_id, artifact.artifact_id, version))
      .then(setText)
      .catch((e) => setError(String(e)));
  }, [artifact.agent_id, artifact.artifact_id, version]);

  if (error) return <div className="p-4 text-red-400">Failed to load: {error}</div>;
  if (!text && !error) return <div className="p-4 opacity-60">(empty markdown)</div>;
  return (
    <div className="markdown-content max-w-none p-4 overflow-auto">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
