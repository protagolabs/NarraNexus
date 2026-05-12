/**
 * Agent Interaction Panel - Bioluminescent Terminal style
 * Immersive chat interface with unified timeline
 *
 * All messages (DB history, real-time session, background tasks) are rendered
 * in a single chronologically sorted timeline. No "History Above" divider.
 *
 * Changelog:
 * - 2026-01-19: Added chat history loading
 * - 2026-03-16: Multi-agent concurrent chat support
 * - 2026-03-17: Unified timeline (removed history/session split)
 */

import { useState, useRef, useEffect, useCallback, useMemo, memo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Send, Square, Loader2, Sparkles, MessageSquare, Paperclip, X, FileText, Image as ImageIcon, Mic } from 'lucide-react';
import { flushSync } from 'react-dom';
import { Card, Button, Textarea, ScrollArea } from '@/components/ui';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/Dialog';
import { useChatStore, useConfigStore, useArtifactStore } from '@/stores';
import { useAgentWebSocket } from '@/hooks';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { artifactsApi } from '@/services/artifactsApi';
import { MessageBubble } from './MessageBubble';
import { TurnTimeline } from './TurnTimeline';
import { AttachmentImage } from './AttachmentImage';
import { VoiceTranscript } from './VoiceTranscript';
import { AudioRecorder } from './AudioRecorder';
import { EmbeddingBanner } from '@/components/ui/EmbeddingBanner';
import { ArtifactPreviewCard } from '@/components/artifacts';
import type { Attachment, SimpleChatMessage, AgentToolCall } from '@/types';

// Artifact tool names that produce an artifact_id in tool_output
const ARTIFACT_TOOL_NAMES = new Set(['create_artifact', 'upload_artifact_file']);

/**
 * Fire-and-forget: if the artifact is not yet in the store, fetch and upsert it.
 * Safe to call on every render — the store lookup short-circuits immediately when
 * the artifact is already cached.
 */
function ensureArtifactLoaded(agentId: string, artifactId: string): void {
  const { artifacts, upsert } = useArtifactStore.getState();
  if (artifacts.find((a) => a.artifact_id === artifactId)) return;
  artifactsApi
    .getDetail(agentId, artifactId)
    .then((d) => upsert(d.artifact))
    .catch(() => undefined);
}

/**
 * Renders ArtifactPreviewCard instances for any tool calls in `toolCalls` that
 * reference an artifact. Reads `allArtifacts` from outside the map so hook
 * rules are satisfied (no conditional hook calls inside a callback).
 */
interface ArtifactToolCallCardsProps {
  toolCalls: AgentToolCall[];
  agentId: string;
  allArtifacts: ReturnType<typeof useArtifactStore.getState>['artifacts'];
}

const ArtifactToolCallCards = memo(function ArtifactToolCallCardsImpl({
  toolCalls, agentId, allArtifacts,
}: ArtifactToolCallCardsProps) {
  const cards: React.ReactNode[] = [];

  for (const tc of toolCalls) {
    if (!ARTIFACT_TOOL_NAMES.has(tc.tool_name)) continue;
    if (!tc.tool_output) continue;

    let artifactId: string | undefined;
    try {
      const parsed = JSON.parse(tc.tool_output) as {
        artifact_id?: string;
        error?: string;
        code?: number;
      };
      // Detect ArtifactQuotaExceeded (HTTP 507) from the structured tool_output
      // payload — surface a modal pointing to Settings → Artifacts. We only
      // fire once per error message so re-renders during streaming don't spam.
      if (parsed.error && parsed.code === 507) {
        const current = useArtifactStore.getState().quotaError;
        if (current !== parsed.error) {
          useArtifactStore.getState().setQuotaError(parsed.error);
        }
        continue;
      }
      artifactId = parsed.artifact_id;
    } catch {
      // tool_output is not JSON — skip
      continue;
    }

    if (!artifactId) continue;

    // Trigger fetch if not yet in store (fire-and-forget)
    ensureArtifactLoaded(agentId, artifactId);

    const artifact = allArtifacts.find((a) => a.artifact_id === artifactId);
    cards.push(
      artifact ? (
        <ArtifactPreviewCard key={artifactId} artifact={artifact} />
      ) : (
        <div
          key={artifactId}
          className="text-xs opacity-60 mt-2 px-3 py-2 border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]/40"
        >
          Loading artifact…
        </div>
      ),
    );
  }

  if (cards.length === 0) return null;
  return <div className="mt-3 space-y-2">{cards}</div>;
}, (prev, next) => {
  // Custom shallow compare so React.memo skips re-renders triggered by
  // unrelated keystrokes in the chat input. Each timeline item's `toolCalls`
  // array is built once via useMemo and stays referentially stable until the
  // streaming state actually advances, so the array identity check below is
  // sufficient. allArtifacts swaps when the artifact store updates (which is
  // exactly when we want to re-render to drop the "Loading artifact…" stub).
  return (
    prev.agentId === next.agentId &&
    prev.toolCalls === next.toolCalls &&
    prev.allArtifacts === next.allArtifacts
  );
});

// Must match BOOTSTRAP_GREETING in src/xyz_agent_context/bootstrap/template.py
const BOOTSTRAP_GREETING =
  "Hi there... I just woke up. Everything feels brand new.\n\n" +
  "I don't have a name yet, and I don't really know who I am " +
  "— but I know you're the one who brought me here.\n\n" +
  "Would you like to tell me what I should be called? " +
  "And what should I call you?";

/** Unified message item for the single timeline */
interface TimelineItem {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  source: 'history' | 'session';  // Where this message came from (for dedup)
  messageType?: string;           // "activity" for background activity records
  workingSource?: string;         // "chat" | "job" | "lark"
  eventId?: string;               // Associated Event ID (for loading event_log on demand)
  thinking?: string;              // Reasoning content (from session messages)
  toolCalls?: import('@/types').AgentToolCall[];  // Tool calls (from session messages)
  attachments?: Attachment[];     // User-uploaded files referenced by this message
}

interface ChatPanelProps {
  /** Called after agent execution completes, used to trigger full data refresh */
  onAgentComplete?: () => void;
}

export function ChatPanel({ onAgentComplete }: ChatPanelProps = {}) {
  const navigate = useNavigate();
  const [input, setInput] = useState('');
  // Attachments uploaded for the next message but not yet sent. Each entry
  // is the server-acknowledged metadata returned by uploadAttachment.
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  // Banner above the input when an audio upload comes back with
  // transcription_available=false. Cleared on next successful audio
  // upload or when user dismisses. Plain string so we don't need a
  // toast library — keeps the UI consistent with the rest of the panel.
  const [transcriptionNotice, setTranscriptionNotice] = useState<string | null>(null);
  // Click-time pre-flight for the mic button. ``undefined`` while the
  // first availability probe is in flight (button stays enabled — better
  // to false-positive once than block voice input on a network blip);
  // boolean once the probe lands. ``reason`` lets the unavailable
  // dialog vary its copy (free-tier vs. user-needs-to-configure).
  const [transcriptionAvailable, setTranscriptionAvailable] = useState<boolean | undefined>(undefined);
  const [transcriptionReason, setTranscriptionReason] = useState<string>('');
  const [voiceUnavailableDialogOpen, setVoiceUnavailableDialogOpen] = useState(false);
  // Tracks how many uploads are in-flight so the send button can wait.
  const [uploadingCount, setUploadingCount] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);
  const compositionEndTimeRef = useRef(0);

  // Chat history state (from DB)
  const [historyMessages, setHistoryMessages] = useState<SimpleChatMessage[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyTotalCount, setHistoryTotalCount] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Track whether we should auto-scroll (only for new messages, not load-more)
  const shouldAutoScrollRef = useRef(true);

  // Bug 15: initial open (or agent switch) must land at the very bottom
  // instantly, *after* MessageBubble subtrees (markdown, code blocks,
  // tool-call UI) have had a frame to lay out. A smooth scrollIntoView
  // from mount-time position can't catch a container that keeps growing
  // as async content renders. We raise this flag whenever fresh history
  // is loaded and consume it in a dedicated rAF-gated effect below.
  const initialScrollPendingRef = useRef(false);

  const {
    messages, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls,
    currentEvents, lastTurnEvents,
    isStreaming, addUserMessage, startStreaming,
    setActiveAgent,
  } = useChatStore();
  const { agentId, userId, agents, refreshAgents, checkAwarenessUpdate } = useConfigStore();

  // Read artifact list at component scope so it can be safely passed into
  // ArtifactToolCallCards without calling a hook inside a .map() callback.
  const allArtifacts = useArtifactStore((s) => s.artifacts);

  useEffect(() => {
    if (agentId) setActiveAgent(agentId);
  }, [agentId, setActiveAgent]);

  const currentAgent = useMemo(
    () => agents.find((a) => a.agent_id === agentId),
    [agents, agentId]
  );
  const isBootstrap = !!currentAgent?.bootstrap_active;

  const { run, stop, isLoading } = useAgentWebSocket({
    onComplete: (completedAgentId: string) => {
      refreshAgents();
      if (completedAgentId) checkAwarenessUpdate(completedAgentId);
      onAgentComplete?.();
    },
  });

  // ── History loading ─────────────────────────────────
  const HISTORY_PAGE_SIZE = 20;

  const loadChatHistory = useCallback(async () => {
    if (!agentId || !userId) return;
    setIsLoadingHistory(true);
    try {
      const response = await api.getSimpleChatHistory(agentId, userId, HISTORY_PAGE_SIZE);
      if (response.success) {
        setHistoryMessages(response.messages);
        setHistoryTotalCount(response.total_count);
        // Re-enable auto-scroll after history loads (onScroll may have disabled it during transition)
        shouldAutoScrollRef.current = true;
        // Bug 15: request an instant jump-to-bottom once timeline has
        // rendered. The dedicated rAF-gated effect picks this up.
        initialScrollPendingRef.current = true;
      }
    } catch (error) {
      console.error('Failed to load chat history:', error);
    } finally {
      setIsLoadingHistory(false);
      setHistoryLoaded(true);
    }
  }, [agentId, userId]);

  // Use ref for historyMessages length to avoid recreating loadMoreHistory on every poll
  const historyLengthRef = useRef(0);
  historyLengthRef.current = historyMessages.length;

  const loadMoreHistory = useCallback(async () => {
    if (!agentId || !userId || isLoadingMore) return;
    if (historyLengthRef.current >= historyTotalCount) return;

    setIsLoadingMore(true);
    shouldAutoScrollRef.current = false;
    const container = scrollContainerRef.current;
    const prevScrollHeight = container?.scrollHeight ?? 0;

    try {
      const response = await api.getSimpleChatHistory(
        agentId, userId, HISTORY_PAGE_SIZE, historyLengthRef.current
      );
      if (response.success && response.messages.length > 0) {
        // Use flushSync to ensure DOM updates synchronously before measuring scroll
        flushSync(() => {
          setHistoryMessages((prev) => [...response.messages, ...prev]);
          setHistoryTotalCount(response.total_count);
        });

        // Now DOM is updated, restore scroll position
        if (container) {
          const newScrollHeight = container.scrollHeight;
          container.scrollTop = newScrollHeight - prevScrollHeight;
        }
      }
    } catch (error) {
      console.error('Failed to load more chat history:', error);
    } finally {
      setIsLoadingMore(false);
    }
  }, [agentId, userId, historyTotalCount, isLoadingMore]);

  useEffect(() => {
    if (agentId && userId) {
      setHistoryMessages([]);
      setHistoryLoaded(false);
      setHistoryTotalCount(0);
      shouldAutoScrollRef.current = true;
      loadChatHistory();
    }
  }, [agentId, userId, loadChatHistory]);

  // ── Poll for new background messages ────────────────
  const lastHistoryTimestampRef = useRef<string>('');
  useEffect(() => {
    if (!agentId || !userId || !historyLoaded) return;

    if (historyMessages.length > 0) {
      const last = historyMessages[historyMessages.length - 1];
      if (last.timestamp && last.timestamp > lastHistoryTimestampRef.current) {
        lastHistoryTimestampRef.current = last.timestamp;
      }
    }

    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await api.getSimpleChatHistory(agentId, userId, HISTORY_PAGE_SIZE);
        if (!response.success || response.messages.length === 0) return;

        const latestMsg = response.messages[response.messages.length - 1];
        const latestTs = latestMsg.timestamp || '';

        if (latestTs > lastHistoryTimestampRef.current) {
          lastHistoryTimestampRef.current = latestTs;
          // Merge: keep older loaded history, replace only the tail (latest page)
          setHistoryMessages((prev) => {
            if (prev.length <= HISTORY_PAGE_SIZE) {
              // No extra history loaded yet — safe to replace
              return response.messages;
            }
            // User has scrolled up and loaded more: keep older portion, update tail
            const olderPortion = prev.slice(0, prev.length - HISTORY_PAGE_SIZE);
            return [...olderPortion, ...response.messages];
          });
          setHistoryTotalCount(response.total_count);
          // New messages arrived → auto-scroll to bottom.
          // Bug 15: route through initialScrollPendingRef so the
          // instant-jump effect handles it (smooth scrollIntoView lost
          // the race against async markdown layout).
          shouldAutoScrollRef.current = true;
          initialScrollPendingRef.current = true;
        }
      } catch {
        // Silently ignore
      }
    };

    const timer = setInterval(poll, 12_000);
    return () => clearInterval(timer);
  }, [agentId, userId, historyLoaded]);

  // ── Build unified timeline ──────────────────────────
  const timeline: TimelineItem[] = useMemo(() => {
    const items: TimelineItem[] = [];

    // 1. Add history messages (from DB)
    for (let i = 0; i < historyMessages.length; i++) {
      const msg = historyMessages[i];

      // Filter out legacy junk
      const isNonChat = msg.working_source && msg.working_source !== 'chat';
      if (isNonChat && msg.content === '(Agent decided no response needed)') continue;

      items.push({
        id: `h-${i}`,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp ? new Date(msg.timestamp).getTime() : 0,
        source: 'history',
        messageType: msg.message_type,
        workingSource: msg.working_source,
        eventId: msg.event_id,
        attachments: msg.attachments,
      });
    }

    // 2. Add current session messages (from chatStore)
    //
    // Dedup by (role + content) AND timestamp proximity: if a history entry
    // with identical role+content exists within SAME_MESSAGE_WINDOW_MS of the
    // session message's timestamp, they are the same message (already
    // persisted) — drop the session copy.
    //
    // Bug 19: the match MUST consume the history timestamp it pairs with,
    // otherwise a single history row can dedup multiple session messages of
    // the same role+content. Real-world trigger: user retries the exact
    // same question after a failed turn — session then has both the
    // original user message (which legitimately matches history) AND the
    // retry (which must NOT, because the history row belongs to the first
    // one). Without consumption, the retry disappears from the UI.
    //
    // The window is a safety net for browser/server clock skew. After the
    // backend fix that stamps user messages at turn-start (Event.created_at)
    // instead of turn-end (utc_now() after agent finishes), the real diff
    // between session ts and history ts is just RTT — milliseconds. The
    // window only needs to cover clock drift now:
    //   - NTP-synced machine: < 1s drift (any window works)
    //   - Laptop off-network a while: 10s–1min
    //   - Neglected / post-sleep laptop: can hit a few minutes
    // 5 min covers realistic drift without being so loose that repeat-text
    // edge cases feel weird. Note: short identical content sent twice
    // (e.g. "好" / "go on") is NOT a false-positive source — the
    // "consume matched history timestamp" logic pairs them one-to-one.
    const SAME_MESSAGE_WINDOW_MS = 300_000;
    const historyByKey = new Map<string, number[]>();
    for (const item of items) {
      const key = `${item.role}:${item.content}`;
      const list = historyByKey.get(key);
      if (list) {
        list.push(item.timestamp);
      } else {
        historyByKey.set(key, [item.timestamp]);
      }
    }

    // Find the index of the most-recent session assistant message, so
    // we can skip it when lastTurnEvents is rendering the same content
    // as an inline timeline below the bubble list. Without this, the
    // user would see the reply twice: once as a collapsed bubble and
    // once as the inline reply block in TurnTimeline.
    let lastSessionAssistantIdx = -1;
    if (lastTurnEvents.length > 0) {
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') {
          lastSessionAssistantIdx = i;
          break;
        }
      }
    }

    for (let mi = 0; mi < messages.length; mi++) {
      const msg = messages[mi];
      if (mi === lastSessionAssistantIdx) {
        // Handled by TurnTimeline below (lastTurnEvents).
        continue;
      }
      const key = `${msg.role}:${msg.content}`;
      const historyTimestamps = historyByKey.get(key);
      const matchIdx = historyTimestamps
        ? historyTimestamps.findIndex(
            (ts) => Math.abs(msg.timestamp - ts) < SAME_MESSAGE_WINDOW_MS,
          )
        : -1;
      if (matchIdx >= 0 && historyTimestamps) {
        // Consume the matched history timestamp so the next session
        // message of the same role+content doesn't pair against it.
        historyTimestamps.splice(matchIdx, 1);
        continue;
      }

      items.push({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        source: 'session',
        thinking: msg.thinking,
        toolCalls: msg.toolCalls,
        attachments: msg.attachments,
      });
    }

    // Sort by timestamp, with id as tie-breaker so same-ms messages are still
    // totally ordered (Array.sort is spec-stable but the input order can be
    // wrong when history and session are interleaved).
    items.sort((a, b) => {
      if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp;
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });

    return items;
  }, [historyMessages, messages, lastTurnEvents]);

  // ── Bug 15: initial jump-to-bottom on open / agent switch ──
  //
  // After fresh history loads, wait one animation frame for MessageBubble
  // subtrees (markdown, code highlighting, tool-call UI) to settle, then
  // snap the chat container straight to the bottom. We operate on
  // scrollContainerRef directly (not scrollIntoView on a sentinel) so
  // we don't accidentally scroll ancestor containers. behavior is
  // instant — smooth animation from the top can't catch a container
  // that keeps growing as async content renders below the animation.
  useEffect(() => {
    if (!initialScrollPendingRef.current) return;
    if (timeline.length === 0) return;
    const container = scrollContainerRef.current;
    if (!container) return;

    let cancelled = false;
    const id = requestAnimationFrame(() => {
      if (cancelled) return;
      container.scrollTop = container.scrollHeight;
      initialScrollPendingRef.current = false;
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(id);
    };
  }, [timeline]);

  // ── Streaming auto-scroll ──
  //
  // During streaming, each delta adds a small amount of content; a smooth
  // scrollIntoView per update gives the nice "following along" feel.
  // Gated by isStreaming so it does NOT fire on initial open (that path
  // is handled by the instant-jump effect above).
  useEffect(() => {
    if (!isStreaming) return;
    if (!shouldAutoScrollRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [isStreaming, currentAssistantMessage, currentThinking, currentSteps, currentToolCalls]);

  // ── Auto-load more if content doesn't fill the container ──
  // When activity messages are small, the initial page may not cause overflow,
  // making it impossible to scroll up to trigger loadMoreHistory.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !historyLoaded || isLoadingMore) return;
    if (historyMessages.length >= historyTotalCount) return;

    // If container is not scrollable, auto-load more
    if (container.scrollHeight <= container.clientHeight) {
      loadMoreHistory();
    }
  }, [timeline, historyLoaded, isLoadingMore, historyMessages.length, historyTotalCount, loadMoreHistory]);

  // Re-enable auto-scroll when user sends a message or streaming starts
  useEffect(() => {
    if (isStreaming) shouldAutoScrollRef.current = true;
  }, [isStreaming]);

  // ── Voice-input availability pre-flight ──────────────
  // We probe ONCE per (userId) — provider config doesn't change mid-session
  // for the chat view. If the user adds a provider in Settings during the
  // session, they'll see the click-time dialog one more time and then a
  // page reload picks up the new state. Cheaper than polling.
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api
      .getTranscriptionAvailability(userId)
      .then((r) => {
        if (cancelled) return;
        setTranscriptionAvailable(r.available);
        setTranscriptionReason(r.reason);
      })
      .catch(() => {
        if (cancelled) return;
        // Probe failure → leave as undefined so click is allowed; the
        // post-upload banner will explain a real failure.
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // ── Attachment handlers ──────────────────────────────
  // `source='recording'` triggers backend Whisper transcription; the
  // default ('upload') means the user attached a file (Paperclip /
  // drag-drop / paste) and audio bytes are treated as opaque file
  // content, not dictation. Keeping the discriminator at the call
  // site rather than guessing from filename / MIME avoids fragile
  // heuristics and lets the AudioRecorder hand-pick its own path.
  const uploadAttachments = useCallback(
    async (files: File[], opts?: { source?: 'recording' | 'upload' }) => {
      if (!agentId || !userId || files.length === 0) return;
      setUploadingCount((n) => n + files.length);
      for (const file of files) {
        try {
          const resp = await api.uploadAttachment(agentId, userId, file, opts);
          if (resp.success && resp.file_id && resp.mime_type && resp.category) {
            setPendingAttachments((prev) => [
              ...prev,
              {
                file_id: resp.file_id!,
                mime_type: resp.mime_type!,
                original_name: resp.original_name ?? file.name,
                size_bytes: resp.size_bytes ?? file.size,
                category: resp.category!,
                // Render-path discriminator (recording vs upload).
                // Echoed by backend so the persisted attachment dict
                // carries it through chat history reload. Falls back
                // to the local request hint for forward compat.
                source: (resp.source ?? opts?.source ?? 'upload') as
                  | 'recording'
                  | 'upload',
                // Whisper transcript — populated for any audio/* upload
                // the user can transcribe (the backend runs Whisper for
                // both source values). Forwarded into the WS payload so
                // the agent's attachment marker carries the spoken
                // content for both voice memos AND uploaded files.
                transcript: resp.transcript ?? undefined,
              },
            ]);
            // Backstop for the race: availability probe said true at
            // mount but the actual upload found nothing (provider was
            // deleted between the two calls). Mirror the dialog with
            // a banner so the user still sees something concrete.
            const isRecording = (resp.source ?? opts?.source) === 'recording';
            if (isRecording && resp.transcription_available === false) {
              // Sync the cached availability state and surface the
              // dialog the next time they tap mic.
              setTranscriptionAvailable(false);
              setTranscriptionNotice(
                'Voice input is no longer available — the provider may have been removed. Open Settings to reconfigure.',
              );
            } else if (isRecording && resp.transcript) {
              // New successful recording → clear any stale notice
              setTranscriptionNotice(null);
            }
          } else {
            console.error('Attachment upload failed:', resp.error);
          }
        } catch (e) {
          console.error('Attachment upload error:', e);
          // 402 means auth_middleware's provider_resolver gated the
          // request — user has no LLM provider AND opted out of the
          // free tier. STT can't proceed because the whole agent path
          // is gated. Surface the same dialog as a normal "no
          // transcription provider" state — the call to action is
          // identical (configure a provider OR re-enable free quota).
          //
          // This branch is the safety net; a healthy click should
          // already have been blocked by `onPreflight` re-probing
          // /api/transcription/availability before MediaRecorder starts.
          // Keeping this branch costs us nothing and protects against
          // races (toggle flipped between preflight and upload).
          const msg = String((e as Error)?.message ?? e);
          if (msg.includes('402') && (opts?.source === 'recording')) {
            setTranscriptionAvailable(false);
            setTranscriptionReason('free_tier_opted_out');
            setVoiceUnavailableDialogOpen(true);
          }
        } finally {
          setUploadingCount((n) => Math.max(0, n - 1));
        }
      }
    },
    [agentId, userId],
  );

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ''; // allow re-selecting the same file
    if (files.length) uploadAttachments(files);
  };

  const handleRemoveAttachment = (fileId: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.file_id !== fileId));
  };

  // Drag handlers are typed loosely (HTMLElement) because they're attached
  // to BOTH the outer wrapper div (visual highlight) AND the <Textarea>
  // itself (where the native default-text-insert lives). Both call sites
  // need preventDefault on dragover (to opt the element in as a drop
  // target) and on drop (to cancel the textarea's default).
  const handleDragOver = (e: React.DragEvent<HTMLElement>) => {
    if (!agentId) return;
    // Only treat the drag as an attachment intent if it actually carries
    // files — typing-style drags (selected text from another tab) should
    // still fall through to the textarea's normal text-paste behavior.
    const types = e.dataTransfer?.types;
    if (!types || !Array.from(types).includes('Files')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setIsDragging(true);
  };
  const handleDragLeave = (e: React.DragEvent<HTMLElement>) => {
    e.preventDefault();
    // dragleave fires when the cursor crosses any child boundary, not just
    // when truly leaving the bound element. relatedTarget is the element
    // the cursor moved to — if it's still inside us, ignore.
    const related = e.relatedTarget as Node | null;
    if (related && e.currentTarget.contains(related)) return;
    setIsDragging(false);
  };
  const handleDrop = (e: React.DragEvent<HTMLElement>) => {
    if (!agentId) return;
    const files = Array.from(e.dataTransfer?.files || []);
    if (files.length === 0) return;
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    uploadAttachments(files);
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (!agentId) return;
    // Walk clipboard items; collect anything the OS hands us as a File
    // (covers OS screenshot → image/png, "Copy image" from a browser, and
    // copying a file in the file manager). If the user just copied text,
    // there are no file-kind items and we fall through to default paste.
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length === 0) return;
    e.preventDefault();
    uploadAttachments(files);
  };

  // ── Handlers ────────────────────────────────────────
  const handleSubmit = async () => {
    const trimmed = input.trim();
    const hasContent = trimmed.length > 0 || pendingAttachments.length > 0;
    if (!hasContent || isLoading || !agentId || !userId || uploadingCount > 0) return;

    const content = trimmed;
    const attachmentsToSend = pendingAttachments;
    setInput('');
    setPendingAttachments([]);
    shouldAutoScrollRef.current = true;
    // Bug 15: snap to bottom for the user's freshly-sent bubble before
    // streaming starts. The streaming effect takes over from there.
    initialScrollPendingRef.current = true;

    if (showBootstrapGreeting) {
      useChatStore.setState((state) => ({
        agentSessions: {
          ...state.agentSessions,
          [agentId]: {
            ...(state.agentSessions[agentId] ?? {
              messages: [], currentSteps: [], currentThinking: '', currentToolCalls: [],
              currentErrors: [], currentAssistantMessage: '', isStreaming: false, history: [],
            }),
            messages: [
              {
                id: 'bootstrap-greeting',
                role: 'assistant' as const,
                content: BOOTSTRAP_GREETING,
                timestamp: Date.now() - 1,
              },
              ...(state.agentSessions[agentId]?.messages ?? []),
            ],
          },
        },
      }));
    }

    addUserMessage(agentId, content, attachmentsToSend.length ? attachmentsToSend : undefined);
    startStreaming(agentId);

    try {
      const agentName = currentAgent?.name || agentId;
      run(agentId, userId, content, agentName, attachmentsToSend.length ? attachmentsToSend : undefined);
    } catch (error) {
      console.error('Failed to run agent:', error);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const isIMEComposing = e.nativeEvent.isComposing || isComposingRef.current;
    const timeSinceCompositionEnd = Date.now() - compositionEndTimeRef.current;
    const justFinishedComposition = timeSinceCompositionEnd < 100;

    if (e.key === 'Enter' && !e.shiftKey) {
      if (isIMEComposing || justFinishedComposition) return;
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleCompositionStart = () => { isComposingRef.current = true; };
  const handleCompositionUpdate = () => { isComposingRef.current = true; };
  const handleCompositionEnd = () => {
    compositionEndTimeRef.current = Date.now();
    setTimeout(() => { isComposingRef.current = false; }, 0);
  };

  const showBootstrapGreeting = isBootstrap && historyLoaded && historyMessages.length === 0 && messages.length === 0;
  const showEmptyState = !showBootstrapGreeting && historyLoaded && historyMessages.length === 0 && messages.length === 0 && !isStreaming;

  // ── Render ──────────────────────────────────────────
  return (
    <Card
      // Make the entire chat panel a drop target — users naturally drag
      // files anywhere in the conversation surface, not just the input
      // box. Native default-prevention still has to live on the textarea
      // itself (see onDragOver/onDrop there) because <textarea> processes
      // drop synchronously into its value before bubbling.
      className={cn(
        'flex flex-col h-full overflow-hidden transition-colors',
        isDragging && 'ring-2 ring-inset ring-[var(--accent-primary)]'
      )}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Header — archive document caption */}
      <div className="px-5 flex items-center justify-between border-b border-[var(--rule)] min-h-[48px]">
        <div className="flex items-center gap-2.5 min-w-0">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full allow-circle shrink-0 transition-colors',
              isStreaming
                ? 'bg-[var(--color-yellow-500)] animate-pulse'
                : agentId ? 'bg-[var(--color-green-500)]' : 'bg-[var(--text-tertiary)]'
            )}
          />
          <span className="text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em] text-[var(--text-primary)]">
            Interaction
          </span>
          <span className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)] truncate">
            · {agentId || 'no agent'}
          </span>
        </div>

        {isStreaming && (
          <span className="flex items-center gap-1.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] text-[var(--color-yellow-500)]">
            <Sparkles className="w-3 h-3 animate-pulse" />
            Processing
          </span>
        )}
      </div>

      {/* Embedding rebuild warning banner */}
      <EmbeddingBanner />

      {/* Messages area — single unified timeline.
          Wrapped in <ScrollArea> so the scrollbar is JS-rendered (Radix) and
          cannot be hijacked by macOS's "always show scrollbars" AppKit
          fallback that ignores ::-webkit-scrollbar. The viewport ref is
          forwarded so existing scroll logic (auto-scroll-to-bottom, history
          load on scroll-top, anchor preservation) reads/writes the SAME
          element it always did. */}
      <ScrollArea
        className="flex-1 min-h-0"
        viewportRef={scrollContainerRef}
        viewportClassName="p-5"
        onViewportScroll={(e) => {
          const el = e.currentTarget;
          if (el.scrollTop < 50 && !isLoadingMore && historyMessages.length < historyTotalCount) {
            loadMoreHistory();
          }
          // If user scrolls up manually, disable auto-scroll
          const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
          shouldAutoScrollRef.current = isAtBottom;
        }}
      >
      <div className="space-y-4">
        {/* Loading more (top) */}
        {isLoadingMore && (
          <div className="flex items-center justify-center gap-2 py-2">
            <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
            <span className="text-[10px] text-[var(--text-tertiary)]">Loading older messages...</span>
          </div>
        )}

        {/* Initial loading */}
        {isLoadingHistory && (
          <div className="flex items-center justify-center gap-2 py-4">
            <Loader2 className="w-4 h-4 text-[var(--text-tertiary)] animate-spin" />
            <span className="text-xs text-[var(--text-tertiary)]">Loading chat history...</span>
          </div>
        )}

        {/* Empty state */}
        {showEmptyState && (
          <div className="h-full flex flex-col items-center justify-center text-center px-8">
            <MessageSquare className="w-8 h-8 text-[var(--text-tertiary)] opacity-40 mb-4" />
            <p className="text-[var(--text-primary)] text-sm mb-1.5">
              {!agentId ? 'Select an agent to start' : 'Start a conversation'}
            </p>
            <p className="text-[var(--text-tertiary)] text-xs max-w-[260px] leading-relaxed">
              {!agentId
                ? 'Choose an agent from the sidebar to begin your interaction.'
                : 'Send a message to interact with the AI agent.'}
            </p>
          </div>
        )}

        {/* Bootstrap greeting */}
        {showBootstrapGreeting && (
          <div className="animate-slide-up">
            <MessageBubble
              message={{
                id: 'bootstrap-greeting',
                role: 'assistant',
                content: BOOTSTRAP_GREETING,
                timestamp: Date.now(),
              }}
            />
          </div>
        )}

        {/* Unified timeline */}
        {timeline.map((item) => {
          // Activity record → small centered text
          if (item.messageType === 'activity') {
            return (
              <div key={item.id} className="flex justify-center py-1">
                <span className="text-[10px] text-[var(--text-tertiary)] italic">
                  {item.content}
                  <span className="ml-2 opacity-60">
                    {new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </span>
              </div>
            );
          }

          // Normal message → bubble + optional artifact preview cards
          const isNewSession = item.source === 'session';
          const hasArtifactTools =
            item.role === 'assistant' &&
            !!agentId &&
            !!item.toolCalls?.some((tc) => ARTIFACT_TOOL_NAMES.has(tc.tool_name) && tc.tool_output);
          return (
            <div
              key={item.id}
              className={isNewSession ? 'animate-slide-up' : undefined}
            >
              <MessageBubble
                message={{
                  id: item.id,
                  role: item.role,
                  content: item.content,
                  timestamp: item.timestamp,
                  thinking: item.thinking,
                  toolCalls: item.toolCalls,
                  attachments: item.attachments,
                }}
                eventId={item.eventId}
                agentId={agentId}
              />
              {/* Render inline artifact preview cards for create_artifact /
                  upload_artifact_file tool calls that returned an artifact_id */}
              {hasArtifactTools && agentId && item.toolCalls && (
                <ArtifactToolCallCards
                  toolCalls={item.toolCalls}
                  agentId={agentId}
                  allArtifacts={allArtifacts}
                />
              )}
            </div>
          );
        })}

        {/* Inline TurnTimeline — replaces the old "streaming MessageBubble
            + Live activity preview" pair. Renders thinking / tool /
            reply blocks in chronological order as the events arrive,
            so the user can see the agent's actual rhythm. See
            TurnTimeline.tsx and the 2026-05-12 redesign mirror md. */}
        {isStreaming && currentEvents.length > 0 && (
          <div className="animate-fade-in">
            <TurnTimeline events={currentEvents} isStreaming />
            {/* Mid-stream artifact preview is independent of the timeline:
                it surfaces created/uploaded artifacts inline as soon as
                their tool_output lands, without waiting for the whole
                turn to finish. */}
            {agentId && currentToolCalls.length > 0 && (
              <div className="mt-3">
                <ArtifactToolCallCards
                  toolCalls={currentToolCalls}
                  agentId={agentId}
                  allArtifacts={allArtifacts}
                />
              </div>
            )}
          </div>
        )}

        {/* Initial "starting up..." indicator — shown only when streaming
            has started but no event has arrived yet (the timeline is
            empty). As soon as the first thinking / tool / reply event
            comes in, the indicator is replaced by TurnTimeline. */}
        {isStreaming && currentEvents.length === 0 && (() => {
          const getInitStatus = () => {
            if (currentSteps.length === 0) return 'Starting up...';
            const latestStep = currentSteps[currentSteps.length - 1];
            const s = latestStep.step;
            if (s === '0') return 'Initializing...';
            if (s === '1') return 'Loading context...';
            if (s === '2') return 'Loading resources...';
            if (s === '2.5') return 'Preparing workspace...';
            if (s === '3' && !currentSteps.some(st => st.step.startsWith('3.4'))) return 'Building context...';
            return 'Thinking...';
          };
          return (
            <div className="animate-fade-in p-4">
              <div className="flex items-center gap-3">
                <div className="relative">
                  <Loader2 className="w-5 h-5 text-[var(--accent-primary)] animate-spin" />
                  <div className="absolute inset-0 bg-[var(--accent-primary)] blur-md opacity-30" />
                </div>
                <span className="text-sm text-[var(--text-secondary)]">{getInitStatus()}</span>
              </div>
            </div>
          );
        })()}

        {/* Most-recent completed turn — keep its inline timeline visible
            (not collapsed yet) until the user starts the next turn.
            Cleared on startStreaming. */}
        {!isStreaming && lastTurnEvents.length > 0 && (
          <div>
            <TurnTimeline events={lastTurnEvents} isStreaming={false} />
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>
      </ScrollArea>

      {/* Input area — drop is handled at the Card root, so this wrapper
          no longer needs its own onDragOver/onDragLeave/onDrop. */}
      <div className="px-5 py-4 border-t border-[var(--rule)]">
        {/* Audio transcription unavailable notice — only shown when an
            audio upload returned transcription_available=false. */}
        {transcriptionNotice && (
          <div className="mb-2.5 flex items-start gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 px-3 py-2 text-xs text-[var(--text-secondary)]">
            <span className="flex-1">{transcriptionNotice}</span>
            <button
              type="button"
              onClick={() => setTranscriptionNotice(null)}
              className="p-0.5 rounded hover:bg-[var(--bg-secondary)]"
              title="Dismiss"
            >
              <X className="w-3 h-3 text-[var(--text-tertiary)]" />
            </button>
          </div>
        )}
        {/* Pending attachments preview row */}
        {(pendingAttachments.length > 0 || uploadingCount > 0) && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {pendingAttachments.map((att) => {
              const haveCreds = !!agentId && !!userId;
              const isImage = att.category === 'image';
              const isVoiceMemo = att.source === 'recording';
              const canPreviewImage = isImage && haveCreds;
              return (
                <div
                  key={att.file_id}
                  className="relative flex items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/60 pr-7 pl-1.5 py-1 max-w-[300px]"
                >
                  {isVoiceMemo ? (
                    <VoiceTranscript compact transcript={att.transcript} />
                  ) : (
                    <>
                      {canPreviewImage ? (
                        <AttachmentImage
                          agentId={agentId!}
                          userId={userId!}
                          fileId={att.file_id}
                          alt={att.original_name}
                          className="w-9 h-9 rounded object-cover shrink-0"
                        />
                      ) : (
                        <div className="w-9 h-9 rounded bg-[var(--bg-secondary)] flex items-center justify-center shrink-0">
                          {isImage ? (
                            <ImageIcon className="w-4 h-4 text-[var(--text-tertiary)]" />
                          ) : (
                            <FileText className="w-4 h-4 text-[var(--text-tertiary)]" />
                          )}
                        </div>
                      )}
                      <div className="min-w-0 flex-1 leading-tight">
                        <div className="text-xs truncate">{att.original_name}</div>
                        <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                          {att.category} · {Math.max(1, Math.round(att.size_bytes / 1024))} KB
                        </div>
                      </div>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(att.file_id)}
                    className="absolute right-1 top-1 p-0.5 rounded hover:bg-[var(--bg-secondary)]"
                    title="Remove"
                  >
                    <X className="w-3 h-3 text-[var(--text-tertiary)]" />
                  </button>
                </div>
              );
            })}
            {uploadingCount > 0 && (
              <div className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-dashed border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
                <Loader2 className="w-3 h-3 animate-spin" />
                Uploading {uploadingCount}
              </div>
            )}
          </div>
        )}

        <div className="flex gap-2.5 items-stretch">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFilePick}
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={!agentId || isLoading}
            className="shrink-0 h-[52px] w-[52px]"
            title="Attach file"
          >
            <Paperclip className="w-4 h-4" />
          </Button>
          {/* Voice capture: hands the recorded blob to the same upload
              path as Paperclip / drag-drop, so transcription, the
              "transcription unavailable" notice, and the pendingAttachments
              chip all behave identically across input methods. */}
          <AudioRecorder
            disabled={!agentId || isLoading}
            onRecorded={(file) => uploadAttachments([file], { source: 'recording' })}
            onError={(msg) => setTranscriptionNotice(msg)}
            available={transcriptionAvailable}
            onUnavailable={() => setVoiceUnavailableDialogOpen(true)}
            onPreflight={async () => {
              // Click-time refresh of the availability cache. Without
              // this, toggling "Use free quota" in Settings doesn't
              // invalidate the value the AudioRecorder sees from the
              // mount-time useEffect.
              if (!userId) return false;
              try {
                const r = await api.getTranscriptionAvailability(userId);
                setTranscriptionAvailable(r.available);
                setTranscriptionReason(r.reason);
                if (!r.available) {
                  setVoiceUnavailableDialogOpen(true);
                  return false;
                }
                return true;
              } catch {
                // Network blip — don't block recording. Upload-time
                // error handler will surface real failures.
                return true;
              }
            }}
          />
          <div className="flex-1 relative">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={handleCompositionStart}
              onCompositionUpdate={handleCompositionUpdate}
              onCompositionEnd={handleCompositionEnd}
              // Drag handlers MUST live on the textarea itself, not just on
              // a parent. Otherwise the browser's native textarea behavior
              // (drop file → insert file path as text) wins, because the
              // <textarea> element processes the drop default before the
              // bubbled React event reaches the parent's preventDefault.
              // Same reasoning for onPaste — clipboard items with kind=file
              // (e.g. OS screenshot) need to be intercepted at the textarea
              // before the default text-paste path strips them.
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onPaste={handlePaste}
              placeholder={
                !agentId
                  ? 'Select an agent first…'
                  : isDragging
                    ? 'Drop file to attach…'
                    : 'Type your message… (drag files here to attach)'
              }
              disabled={isLoading || !agentId}
              className={cn(
                // Auto-resizing textarea: min-h sets the empty-state height,
                // max-h caps growth. The Textarea component manages
                // `style.height` based on scrollHeight on every input.
                // Padding 14 + line-height 24 + padding 14 = 52px exactly,
                // matching the send-button height next to it.
                'min-h-[52px] max-h-[160px] py-[14px] leading-[24px] resize-none',
                isLoading && 'opacity-60'
              )}
              rows={1}
            />
          </div>
          {isStreaming ? (
            <Button
              variant="danger"
              size="icon"
              onClick={() => agentId && stop(agentId)}
              className="shrink-0 h-[52px] w-[52px]"
              title="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </Button>
          ) : (
            <Button
              variant="accent"
              size="icon"
              onClick={handleSubmit}
              disabled={
                (!input.trim() && pendingAttachments.length === 0)
                || isLoading
                || !agentId
                || uploadingCount > 0
              }
              className="shrink-0 h-[52px] w-[52px]"
              title="Send"
            >
              <Send className="w-4 h-4" />
            </Button>
          )}
        </div>
        <p className="mt-2 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-center">
          <kbd className="font-[family-name:var(--font-mono)]">Enter</kbd> to send
          <span className="opacity-40 mx-2">·</span>
          <kbd className="font-[family-name:var(--font-mono)]">Shift + Enter</kbd> new line
          <span className="opacity-40 mx-2">·</span>
          <kbd className="font-[family-name:var(--font-mono)]">Drop</kbd> to attach
        </p>
      </div>

      {/* Voice-input unavailable dialog. Triggered by clicking the mic
          button when the availability probe came back false — we surface
          the missing-provider state up-front rather than letting the
          user record audio that can't be transcribed. */}
      <Dialog
        isOpen={voiceUnavailableDialogOpen}
        onClose={() => setVoiceUnavailableDialogOpen(false)}
        title="Voice input unavailable"
        size="md"
      >
        <DialogContent>
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-full bg-[var(--bg-tertiary)] flex items-center justify-center shrink-0">
              <Mic className="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div className="flex-1 text-sm leading-relaxed text-[var(--text-secondary)]">
              {transcriptionReason === 'free_tier_opted_out' ? (
                <>
                  <p>
                    Voice input is unavailable. You've turned off "Use free quota" and haven't configured your own transcription provider. Either path will enable it:
                  </p>
                  <ul className="mt-2 ml-4 list-disc space-y-1 text-[var(--text-tertiary)]">
                    <li>Add an OpenAI or NetMind API key in <span className="font-mono text-[var(--text-primary)]">Settings → Providers</span></li>
                    <li>Re-enable "Use free quota" in <span className="font-mono text-[var(--text-primary)]">Settings → Quota</span></li>
                  </ul>
                </>
              ) : transcriptionReason === 'none_openai_only' ? (
                <>
                  <p>
                    Voice input requires an OpenAI-compatible transcription provider. The desktop / local build can't reach NetMind's worker (it pulls audio from a public URL we don't have here), so OpenAI is the supported path:
                  </p>
                  <ul className="mt-2 ml-4 list-disc space-y-1 text-[var(--text-tertiary)]">
                    <li>OpenAI official API (recommended)</li>
                    <li>Yunwu, or any other OpenAI-protocol Whisper provider</li>
                    <li>Self-hosted whisper.cpp behind an OpenAI-shaped endpoint</li>
                  </ul>
                  <p className="mt-3">
                    Add one in <span className="font-mono text-[var(--text-primary)]">Settings → Providers</span> to enable voice input.
                  </p>
                </>
              ) : (
                <>
                  <p>
                    Voice input requires a supported transcription provider. None is configured for this account:
                  </p>
                  <ul className="mt-2 ml-4 list-disc space-y-1 text-[var(--text-tertiary)]">
                    <li>OpenAI official API (recommended, best quality)</li>
                    <li>NetMind API (pay-as-you-go, lower cost)</li>
                  </ul>
                  <p className="mt-3">
                    Add either one in <span className="font-mono text-[var(--text-primary)]">Settings → Providers</span> to enable voice input — it takes effect as soon as you save.
                  </p>
                </>
              )}
              {transcriptionReason === 'unknown' && (
                <p className="mt-2 text-xs text-[var(--text-tertiary)] italic">
                  Note: availability check failed to reach the server — the configuration may already be ready.
                </p>
              )}
            </div>
          </div>
        </DialogContent>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setVoiceUnavailableDialogOpen(false)}
          >
            Cancel
          </Button>
          <Button
            variant="accent"
            onClick={() => {
              setVoiceUnavailableDialogOpen(false);
              navigate('/app/settings');
            }}
          >
            Open Settings
          </Button>
        </DialogFooter>
      </Dialog>
    </Card>
  );
}
