/**
 * @file_name: LogViewer.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Real-time log viewer with service filtering and auto-scroll
 *
 * Displays color-coded log entries grouped by service, with filter tabs
 * and automatic scrolling to the latest entry.
 */

import { useState, useEffect, useRef, useMemo } from 'react';
import { ArrowDownToLine } from 'lucide-react';
import { Card, CardHeader, CardTitle, Button } from '@/components/ui';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import type { LogEntry } from '@/types/platform';

interface LogViewerProps {
  logs: LogEntry[];
  serviceFilter?: string | null;
  maxEntries?: number;
}

/** Color mapping per service ID */
const SERVICE_COLORS: Record<string, string> = {
  backend: 'text-blue-400',
  mcp: 'text-purple-400',
  poller: 'text-green-400',
  frontend: 'text-cyan-400',
};

function getServiceColor(serviceId: string): string {
  return SERVICE_COLORS[serviceId] ?? 'text-[var(--text-secondary)]';
}

function formatLogTimestamp(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LogViewer({
  logs,
  serviceFilter: initialFilter = null,
  maxEntries = 500,
}: LogViewerProps) {
  const [activeFilter, setActiveFilter] = useState<string | null>(
    initialFilter ?? null,
  );
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Derive unique service IDs from logs
  const serviceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const entry of logs) {
      ids.add(entry.serviceId);
    }
    return Array.from(ids).sort();
  }, [logs]);

  // Filter and limit logs
  const visibleLogs = useMemo(() => {
    let filtered = logs;
    if (activeFilter) {
      filtered = logs.filter((l) => l.serviceId === activeFilter);
    }
    if (filtered.length > maxEntries) {
      filtered = filtered.slice(filtered.length - maxEntries);
    }
    return filtered;
  }, [logs, activeFilter, maxEntries]);

  // Auto-scroll on new entries
  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [visibleLogs, autoScroll]);

  // Detect manual scrolling to toggle auto-scroll
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(atBottom);
  };

  return (
    <Card variant="sunken" noPadding>
      <CardHeader>
        <CardTitle>Logs</CardTitle>
        <div className="flex items-center gap-2">
          {!autoScroll && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setAutoScroll(true);
                bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
              }}
              title="Scroll to bottom"
            >
              <ArrowDownToLine className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      </CardHeader>

      {/* Service filter tabs */}
      {serviceIds.length > 0 && (
        <div className="px-4 pt-3">
          <Tabs
            value={activeFilter ?? '__all__'}
            onValueChange={(v) =>
              setActiveFilter(v === '__all__' ? null : v)
            }
          >
            <TabsList>
              <TabsTrigger value="__all__">All</TabsTrigger>
              {serviceIds.map((id) => (
                <TabsTrigger key={id} value={id}>
                  {id}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>
      )}

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-72 overflow-y-auto p-4 font-mono text-xs leading-relaxed"
      >
        {visibleLogs.length === 0 ? (
          <p className="text-[var(--text-tertiary)] text-center py-8">
            No log entries
          </p>
        ) : (
          visibleLogs.map((entry, i) => (
            <div key={i} className="flex gap-2 hover:bg-[var(--bg-secondary)]/30 px-1 rounded">
              <span className="text-[var(--text-tertiary)] shrink-0 select-none">
                {formatLogTimestamp(entry.timestamp)}
              </span>
              <span
                className={cn(
                  'shrink-0 w-16 text-right select-none',
                  getServiceColor(entry.serviceId),
                )}
              >
                [{entry.serviceId}]
              </span>
              <span
                className={cn(
                  'flex-1 break-all',
                  entry.stream === 'stderr'
                    ? 'text-[var(--color-error)]'
                    : 'text-[var(--text-primary)]',
                )}
              >
                {entry.message}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </Card>
  );
}

export type { LogViewerProps };
