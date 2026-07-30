/**
 * SegmentedReply — 把一轮的 Segment[] 渲染成 agent 实际说话的那几次。
 *
 * 一轮可能说多次话（n 次工具调用里有 m 次是回复用户）。后端仍是一轮一条
 * 记录；这里把那一条渲染成 m 个气泡，每个带上导致它的过程。
 *
 * 同一个组件服务直播与历史，差别只有一个开关：
 *   - 直播中（showProcess=false）：过程在 composer 上方的 ProcessPanel 里，
 *     这里只出答案，否则同一份过程会在两处各渲染一遍；
 *   - 结束后（showProcess=true）：面板已卸载，过程折叠回各段自己的气泡上。
 *
 * 段的切法由 lib/segmentTurn 决定——这里只负责画。
 */
import { memo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import type { Segment } from '@/types';
import { Markdown } from '@/components/ui';
import { cn } from '@/lib/utils';
import { TurnTimeline } from './TurnTimeline';

export interface SegmentedReplyProps {
  segments: Segment[];
  /** 折叠的过程详情是否可见（历史气泡为 true，直播为 false）。 */
  showProcess?: boolean;
  /** 直播中：最后一段仍在增长，给它一个流式光标。 */
  isStreaming?: boolean;
}

type FallbackKind = 'none' | 'no_reply' | 'after_error';

// 原属 TurnTimeline 的 ReplyBlock；答案层迁到这里后徽标跟着走（2026-07-30）。
// legacy 'helper_llm_fallback' 是 helper_llm_no_reply 改名前（2026-05-25）的
// 持久化值，等价处理，让老记录仍显示恢复徽标而不是什么都没有。
function fallbackKindFromReplyVia(via: string | undefined): FallbackKind {
  if (via === 'helper_llm_after_error') return 'after_error';
  if (via === 'helper_llm_no_reply' || via === 'helper_llm_fallback') return 'no_reply';
  return 'none';
}

export const SegmentedReply = memo(function SegmentedReply({
  segments,
  showProcess = false,
  isStreaming = false,
}: SegmentedReplyProps) {
  const { t } = useTranslation();
  // 展开状态按段索引记在本组件里：父组件在流式期间每个 delta 都重渲染，
  // 状态放这儿才不会被重置。
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  return (
    <div className="space-y-3">
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        const open = !!expanded[index];
        return (
          <div key={index} className="space-y-1">
            {showProcess && segment.process.length > 0 && (
              <div data-testid={`segment-details-${index}`}>
                <button
                  type="button"
                  onClick={() => setExpanded((prev) => ({ ...prev, [index]: !prev[index] }))}
                  className="flex items-center gap-1 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                >
                  <ChevronRight
                    className={cn('h-3 w-3 transition-transform', open && 'rotate-90')}
                  />
                  {t('chat.segment.details', '推理与工具')} ({segment.process.length})
                </button>
                {open && <TurnTimeline events={segment.process} />}
              </div>
            )}

            {segment.reply && (
              <div
                data-testid={`segment-reply-${index}`}
                className="markdown-content text-[var(--text-primary)]"
              >
                {fallbackKindFromReplyVia(segment.reply.via) === 'no_reply' && (
                  // Soft / informational: the agent finished thinking but didn't
                  // call the reply tool; helper_llm wrote what it should have.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-silicon)' }}
                    title={t('chat.timeline.helperFallbackTip')}
                  >
                    {t('chat.timeline.helperFallback')}
                  </div>
                )}
                {fallbackKindFromReplyVia(segment.reply.via) === 'after_error' && (
                  // Warning: a step in this turn actually failed. helper_llm
                  // wrote a recovery reply from what completed.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-warning)' }}
                    title={t('chat.timeline.recoveredAfterErrorTip')}
                  >
                    {t('chat.timeline.recoveredAfterError')}
                  </div>
                )}
                <Markdown content={segment.reply.content} />
                {isStreaming && isLast && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-[var(--accent-primary)] align-text-bottom" />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
