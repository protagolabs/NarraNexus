/**
 * TurnTimeline — inline block-by-block rendering of a single agent
 * turn's events (thinking · tool_call · tool_output · reply ·
 * native_output), in the order they arrived.
 *
 * Design (per 2026-05-12 review with Xiong):
 * - Each event is its own visual block; blocks are stacked in
 *   chronological order so the user sees the agent's actual rhythm
 *   ("think → tool → think → tool → reply → think").
 * - reply is the user-facing speech and is visually elevated
 *   (border + bigger type + markdown rendering).
 * - thinking is the agent's internal monologue, intentionally
 *   visually demoted (italic, smaller, muted) so it doesn't compete
 *   with reply for attention. Per-block expand/collapse lets users
 *   skim or dive in.
 * - tool_call is a single-line affordance (icon + tool name + one-
 *   line arg summary). Full args/output stay in the right-side
 *   Execution panel; chat keeps it terse.
 *
 * Per-block expand/collapse state is keyed by event.id and lives in
 * this component's local state — fine because the parent ChatPanel
 * keeps the same TurnTimeline mounted across re-renders during a
 * single turn (clears on next user submit).
 */
import { useState, useMemo } from 'react';
import { Brain, Wrench, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react';
import type { TurnEvent } from '@/types';
import { Markdown } from '@/components/ui';
import { cn } from '@/lib/utils';

interface TurnTimelineProps {
  events: TurnEvent[];
  /** When true, the timeline is animated as it grows (e.g. fade-in on
   *  new event). When false (e.g. completed turn still in view), the
   *  blocks render in their final, settled state. */
  isStreaming?: boolean;
}

const THINKING_PREVIEW_CHAR_LIMIT = 280;
const TOOL_ARGS_PREVIEW_CHAR_LIMIT = 80;

function ThinkingBlock({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = content.length > THINKING_PREVIEW_CHAR_LIMIT;
  const visibleText = expanded || !isLong
    ? content
    : content.slice(0, THINKING_PREVIEW_CHAR_LIMIT) + '…';

  return (
    <div
      className={cn(
        'pl-3 border-l-2 border-[var(--rule)] text-[var(--text-tertiary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono mb-1">
        <Brain className="w-3 h-3" />
        <span>Thinking</span>
      </div>
      <div className="text-xs italic leading-relaxed whitespace-pre-wrap">
        {visibleText}
      </div>
      {isLong && (
        <button
          onClick={() => setExpanded((p) => !p)}
          className="mt-1 text-[10px] underline opacity-60 hover:opacity-100"
        >
          {expanded ? 'collapse' : `show full (${content.length} chars)`}
        </button>
      )}
    </div>
  );
}

function ToolCallBlock({
  toolName,
  toolInput,
  isStreaming,
}: {
  toolName: string;
  toolInput: Record<string, unknown>;
  isStreaming: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  // Display the tool name without the long MCP namespace prefix —
  // "mcp__chat_module__get_chat_history" → "get_chat_history".
  const friendlyName = useMemo(() => {
    const parts = toolName.split('__');
    return parts[parts.length - 1] || toolName;
  }, [toolName]);

  // One-line argument preview — first 80 chars of JSON-stringified
  // input (truncated). Full args available on expand.
  const argsJson = useMemo(() => {
    try {
      return JSON.stringify(toolInput);
    } catch {
      return '';
    }
  }, [toolInput]);
  const argsPreview = argsJson.length > TOOL_ARGS_PREVIEW_CHAR_LIMIT
    ? argsJson.slice(0, TOOL_ARGS_PREVIEW_CHAR_LIMIT) + '…'
    : argsJson;

  return (
    <div
      className={cn(
        'flex items-start gap-2 text-xs font-mono text-[var(--text-secondary)]',
        'px-3 py-1.5 rounded-md bg-[var(--bg-tertiary)]/40 border border-[var(--border-subtle)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <Wrench className="w-3.5 h-3.5 mt-0.5 text-[var(--accent-primary)] shrink-0" />
      <div className="min-w-0 flex-1">
        <button
          onClick={() => setExpanded((p) => !p)}
          className="flex items-center gap-1 w-full text-left"
        >
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          <span className="font-semibold text-[var(--text-primary)]">{friendlyName}</span>
          {!expanded && argsPreview && (
            <span className="ml-2 truncate text-[var(--text-tertiary)]">{argsPreview}</span>
          )}
        </button>
        {expanded && (
          <pre className="mt-1.5 text-[10px] whitespace-pre-wrap break-all text-[var(--text-tertiary)]">
            {argsJson}
          </pre>
        )}
      </div>
    </div>
  );
}

function ToolOutputBlock({
  toolName,
  output,
  isStreaming,
}: {
  toolName: string;
  output: string;
  isStreaming: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={cn(
        'pl-3 text-[10px] font-mono text-[var(--text-tertiary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <button
        onClick={() => setExpanded((p) => !p)}
        className="flex items-center gap-1"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <span>output · {toolName.split('__').pop()}</span>
      </button>
      {expanded && (
        <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-all">
          {output}
        </pre>
      )}
    </div>
  );
}

function ReplyBlock({
  content,
  isStreaming,
  isFallback,
}: {
  content: string;
  isStreaming: boolean;
  isFallback: boolean;
}) {
  return (
    <div
      className={cn(
        'message-assistant px-4 py-3',
        'border-l-4 border-[var(--accent-primary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      {isFallback && (
        <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--color-yellow-500)] mb-1.5">
          <span>↻ helper_llm fallback</span>
        </div>
      )}
      <div className="text-sm leading-relaxed">
        <Markdown content={content} />
      </div>
    </div>
  );
}

function NativeOutputBlock({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming: boolean;
}) {
  return (
    <div
      className={cn(
        'message-assistant px-4 py-3 opacity-80',
        'border-l-2 border-dashed border-[var(--text-tertiary)]',
        isStreaming && 'animate-fade-in',
      )}
    >
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-tertiary)] mb-1.5">
        <MessageSquare className="w-3 h-3" />
        <span>Native output</span>
      </div>
      <div className="text-sm leading-relaxed whitespace-pre-wrap">{content}</div>
    </div>
  );
}

export function TurnTimeline({ events, isStreaming = false }: TurnTimelineProps) {
  if (events.length === 0) return null;

  return (
    <div className="space-y-3">
      {events.map((event) => {
        switch (event.type) {
          case 'thinking':
            return (
              <ThinkingBlock
                key={event.id}
                content={event.content}
                isStreaming={isStreaming}
              />
            );
          case 'tool_call':
            return (
              <ToolCallBlock
                key={event.id}
                toolName={event.tool_name}
                toolInput={event.tool_input}
                isStreaming={isStreaming}
              />
            );
          case 'tool_output':
            return (
              <ToolOutputBlock
                key={event.id}
                toolName={event.tool_name}
                output={event.output}
                isStreaming={isStreaming}
              />
            );
          case 'reply':
            return (
              <ReplyBlock
                key={event.id}
                content={event.content}
                isStreaming={isStreaming}
                isFallback={event.reply_via === 'helper_llm_fallback'}
              />
            );
          case 'native_output':
            return (
              <NativeOutputBlock
                key={event.id}
                content={event.content}
                isStreaming={isStreaming}
              />
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
