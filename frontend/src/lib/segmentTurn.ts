/**
 * segmentTurn — 把一轮的事件序列按「用户可见片段」切段。
 *
 * 一轮天然长这样：[思考, 工具, 工具, 回复₁, 思考, 工具, 回复₂, …]。
 * 气泡要显示的是回复，而每个回复之前的过程属于它。所以在每个用户可见
 * 片段处切开即可。
 *
 * 同一个函数服务三条路径——运行中的 currentEvents、刚结束的
 * message.timeline、刷新后 /event-log 的 timeline（经 timelineToEvents
 * 归一）——所以直播看到的和刷新后看到的必然一致：不是两份实现碰巧对上，
 * 而是同一份实现。
 */
import type {
  EventLogTimelineEntry,
  ProcessEvent,
  Segment,
  SegmentReply,
  TurnEvent,
} from '@/types';

const isProcess = (e: TurnEvent): e is ProcessEvent =>
  e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output';

export function segmentTurn(events: TurnEvent[]): Segment[] {
  const segments: Segment[] = [];
  let process: ProcessEvent[] = [];

  const push = (reply: SegmentReply | null) => {
    segments.push({ process, reply });
    process = [];
  };

  for (const event of events) {
    if (isProcess(event)) {
      process.push(event);
      continue;
    }
    // plan 不属于任何一段：它是「现在到哪了」，由面板底部单独渲染。
    if (event.type === 'plan') continue;

    if (event.type === 'reply') {
      push({ content: event.content, via: event.reply_via, streaming: event.streaming });
      continue;
    }

    // native_output：连续的原生文本是同一句话被拆成了多个 delta，合并进
    // 上一段；一旦中间夹了工具调用，那就是 agent 又说了一次，开新段。
    const last = segments[segments.length - 1];
    const mergeable =
      !!last && !!last.reply && last.reply.via === undefined && process.length === 0;
    if (mergeable && last.reply) {
      last.reply = { ...last.reply, content: last.reply.content + event.content };
    } else {
      push({ content: event.content });
    }
  }

  // 末尾残留过程归到最后一段——「说完最后一句还干了活」正是该被看见的。
  // 整轮没有任何回复时，产出一个 reply=null 的段，过程不丢。
  if (process.length > 0) {
    const last = segments[segments.length - 1];
    if (last) last.process = [...last.process, ...process];
    else push(null);
  }
  return segments;
}

/**
 * /event-log 的 timeline → TurnEvent[]。
 *
 * 这段逻辑原先内联在 MessageBubble 里并且**故意跳过 reply**（因为回复文本
 * 已经在 message.content 里渲染过一次）。切段需要 reply 作为切点，所以这里
 * 保留它——重复渲染改由「气泡只渲染 segment.reply」来避免。
 */
export function timelineToEvents(timeline: EventLogTimelineEntry[]): TurnEvent[] {
  const events: TurnEvent[] = [];
  timeline.forEach((entry, idx) => {
    const id = `tl-${idx}`;
    const ts = idx;
    switch (entry.type) {
      case 'thinking':
        if (entry.content) events.push({ id, ts, type: 'thinking', content: entry.content });
        break;
      case 'tool_call': {
        const toolName = entry.tool_name || 'unknown';
        // 持久化 timeline 没有 type='reply'：回复以 send_message 工具调用
        // 的形式存储（tool_input.content 即回复文本）。直播路径 chatStore
        // 做同样的 tool_call→reply 转换，这里对齐它，刷新后才切得出段。
        // 回执 tool_output 照旧作为过程事件——直播也是这么进 currentEvents 的。
        if (toolName.includes('send_message_to_user_directly')) {
          const content = (entry.tool_input as Record<string, unknown> | undefined)?.content;
          if (typeof content === 'string' && content) {
            events.push({ id, ts, type: 'reply', content, reply_via: entry.reply_via });
          }
          break;
        }
        events.push({
          id, ts, type: 'tool_call',
          tool_name: toolName,
          tool_input: entry.tool_input || {},
          reply_via: entry.reply_via,
        });
        break;
      }
      case 'tool_output':
        events.push({
          id, ts, type: 'tool_output',
          tool_name: entry.tool_name || 'unknown',
          output: entry.tool_output || '',
        });
        break;
      case 'native_output':
        if (entry.content) events.push({ id, ts, type: 'native_output', content: entry.content });
        break;
      case 'reply':
        if (entry.content) {
          events.push({ id, ts, type: 'reply', content: entry.content, reply_via: entry.reply_via });
        }
        break;
    }
  });
  return events;
}
