/**
 * @file_name: ChartRenderer.tsx
 * @description: Lazy-loaded renderer for application/vnd.echarts+json artifacts.
 *
 * ECharts (~700 KB) is loaded via dynamic import() so it is never included in
 * the initial bundle. The import fires only when this component mounts, which
 * itself only happens when the user opens an artifact tab whose kind is
 * application/vnd.echarts+json.
 *
 * The JSON artifact content is expected to be a valid ECharts `option` object
 * (https://echarts.apache.org/en/option.html). The agent is responsible for
 * emitting well-formed option JSON; this renderer makes no attempt to validate
 * or repair the payload.
 *
 * Cleanup: the `disposed` flag guards against setState-after-unmount races and
 * `chart.dispose()` is called in the cleanup function to release the canvas.
 */

import { useEffect, useRef, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';
import { useArtifactStore, type ChartInstanceLike } from '@/stores/artifactStore';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function ChartRenderer({ artifact, version }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const registerChartInstance = useArtifactStore((s) => s.registerChartInstance);

  useEffect(() => {
    setError(null);
    let disposed = false;
    let chart: { dispose: () => void } | null = null;

    (async () => {
      try {
        const echarts = await import('echarts');
        const r = await fetch(rawUrl(artifact.agent_id, artifact.artifact_id, version));
        const option = await r.json();
        if (disposed || !ref.current) return;
        const c = echarts.init(ref.current);
        c.setOption(option);
        chart = c as unknown as { dispose: () => void };
        // Register so ArtifactDownloadMenu can call getDataURL() for PNG/JPEG export.
        registerChartInstance(artifact.artifact_id, c as unknown as ChartInstanceLike);
      } catch (e) {
        setError(String(e));
      }
    })();

    return () => {
      disposed = true;
      registerChartInstance(artifact.artifact_id, null);
      chart?.dispose();
    };
  }, [artifact.agent_id, artifact.artifact_id, version, registerChartInstance]);

  if (error) return <div className="p-4 text-red-400">Chart failed: {error}</div>;
  return <div ref={ref} className="w-full h-full" />;
}
