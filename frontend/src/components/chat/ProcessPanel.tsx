/**
 * ProcessPanel — Agent 干活时，过程在这里；答案在上面的气泡里。
 *
 * 为什么单独一个面板：过程和回复原先按时间顺序混在同一条 TurnTimeline 里，
 * 靠边框实线/虚线分层。信息不缺，但读的人要自己在噪音里找答案。分开之后
 * 气泡只有答案，过程在这里连续滚动——像 terminal 一样可以扫，不必读。
 *
 * plan 钉在底部、不参与滚动：它是「现在到哪了」的答案，不该被滚走。plan 是
 * 全量快照语义（replace-on-write），所以只渲染最后一份。
 *
 * 只在运行中挂载；结束后由 ChatPanel 卸载，过程改为按回复切段折叠回各自
 * 气泡（见 lib/segmentTurn）。所以这里不做任何持久化——它是一块取景窗，
 * 不是存储。
 */
import { memo, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { TurnEvent } from '@/types';
import { cn } from '@/lib/utils';

export interface ProcessPanelProps {
  events: TurnEvent[];
}

const PLAN_MARK: Record<string, string> = {
  completed: '✅',
  in_progress: '▶',
  pending: '○',
};

/** 面板最高占视口的比例——再高就把输入框挤出视野了。 */
const MAX_HEIGHT_CLASS = 'max-h-[40vh]';

/** 距底部多少像素内算「仍在跟随」。一次滚轮大约 100px，24 足够区分
 *  「用户往上翻」和「浏览器滚动的舍入误差」。 */
const FOLLOW_THRESHOLD_PX = 24;

export const ProcessPanel = memo(function ProcessPanel({ events }: ProcessPanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  const process = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [events],
  );

  // plan 是全量快照：后一次更新整份替换前一次，所以取最后一条。
  const plan = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (e.type === 'plan') return e;
    }
    return undefined;
  }, [events]);

  // 自动滚到底，除非用户手动往上滚过——与消息区同一套取舍：跟随是默认，
  // 用户一旦表达了「我要看上面」就不再抢走视口。
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [process, plan]);

  if (process.length === 0 && !plan) return null;

  return (
    <div
      data-testid="process-panel"
      className="mb-2 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]"
    >
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          followRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
        className={cn(
          MAX_HEIGHT_CLASS,
          'space-y-1 overflow-y-auto px-3 py-2 font-mono text-xs',
        )}
      >
        {process.map((event) => {
          if (event.type === 'thinking') {
            return (
              <div
                key={event.id}
                className="whitespace-pre-wrap text-[var(--text-tertiary)]"
              >
                · {event.content}
              </div>
            );
          }
          if (event.type === 'tool_call') {
            // 显示第一个参数值作为一行摘要；参数没到就是省略号——这正是
            // pending 的可见形态：名字已经确定，参数还在写。
            const firstArg = Object.values(event.tool_input ?? {})[0];
            return (
              <div
                key={event.id}
                data-testid={`tool-row-${event.id}`}
                data-pending={event.pending ? 'true' : 'false'}
                className="flex items-center gap-2 text-[var(--text-secondary)]"
              >
                {event.pending ? (
                  <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                ) : (
                  <span className="shrink-0">⚙</span>
                )}
                <span className="text-[var(--accent-primary)]">{event.tool_name}</span>
                <span className="truncate text-[var(--text-tertiary)]">
                  {event.pending ? '…' : String(firstArg ?? '')}
                </span>
              </div>
            );
          }
          return (
            <div key={event.id} className="truncate pl-4 text-[var(--text-tertiary)]">
              ↳ {event.output}
            </div>
          );
        })}
      </div>

      {plan && (
        <div
          data-testid="process-plan"
          className="space-y-0.5 border-t border-[var(--border-subtle)] px-3 py-2 text-xs"
        >
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
            {t('chat.process.plan', 'Plan')}
          </div>
          {plan.steps.map((s, i) => (
            <div
              key={`${i}-${s.step}`}
              className={cn(
                'flex gap-2',
                s.status === 'in_progress' && 'text-[var(--accent-primary)]',
                s.status === 'completed' && 'text-[var(--text-tertiary)] line-through',
              )}
            >
              <span className="shrink-0">{PLAN_MARK[s.status] ?? '○'}</span>
              <span>{s.step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
