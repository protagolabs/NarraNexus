/**
 * @file_name: CsvRenderer.tsx
 * @description: Lazy-loaded renderer for text/csv artifacts.
 *
 * Fetches the raw CSV text and renders it as a scrollable HTML table.
 * Uses a naive comma-split parser — good enough for agent-generated tabular
 * output. Does NOT handle quoted fields containing commas (e.g. "a,b",c).
 * If the project ever needs proper RFC 4180 parsing, swap parseCsv() for
 * papaparse or csv-parse without touching any other part of this component.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';

interface Props {
  artifact: Artifact;
  version: number;
}

function parseCsv(text: string): string[][] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => line.split(','));
}

export default function CsvRenderer({ artifact, version }: Props) {
  const [rows, setRows] = useState<string[][] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(rawUrl(artifact.agent_id, artifact.artifact_id, version))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => setRows(parseCsv(text)))
      .catch((e) => setError(String(e)));
  }, [artifact.agent_id, artifact.artifact_id, version]);

  if (error) return <div className="p-4 text-red-400">Failed to load: {error}</div>;
  if (!rows) return <div className="p-4 opacity-60">Loading…</div>;

  const [header, ...body] = rows;
  return (
    <div className="overflow-auto p-4">
      <table className="border-collapse text-sm">
        <thead>
          <tr>
            {header.map((cell, i) => (
              <th key={i} className="border border-[var(--border-default)] px-2 py-1 text-left bg-[var(--bg-primary)]">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="border border-[var(--border-default)] px-2 py-1">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
