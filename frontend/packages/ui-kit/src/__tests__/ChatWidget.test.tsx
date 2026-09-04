/**
 * @file_name: ChatWidget.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: ChatWidget renders history, streams a reply into one assistant bubble, disables the composer while busy and shows transport errors.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ChatWidget } from '../ChatWidget';

class FakeSocket {
  static last: FakeSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];
  constructor() {
    FakeSocket.last = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {}
  emit(m: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

const fetchOk = (async () => ({
  ok: true,
  json: async () => ({ events: [{ event_id: 'e1', trigger: 'earlier', final_output: 'reply', created_at: '2026-09-04T00:00:00Z' }] }),
})) as unknown as typeof fetch;

describe('ChatWidget', () => {
  it('loads history, streams a reply and blocks the composer while busy', async () => {
    render(<ChatWidget agentId="a1" userId="u1" baseUrl="http://x" fetch={fetchOk} webSocket={FakeSocket as unknown as typeof WebSocket} />);
    expect(await screen.findByText('earlier')).toBeInTheDocument();
    expect(screen.getByText('reply')).toBeInTheDocument();
    const input = screen.getByLabelText('message') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'hello' } });
    fireEvent.click(screen.getByText('Send'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText('hello')).toBeInTheDocument();
    expect(screen.getByText('Stop')).toBeInTheDocument();
    expect(input.disabled).toBe(true);
    act(() => FakeSocket.last!.emit({ type: 'agent_response', delta: 'wor', response_type: 'text' }));
    act(() => FakeSocket.last!.emit({ type: 'agent_reply_delta', delta: 'ld', call_id: 'c', tool_name: 't' }));
    expect(screen.getByText('world')).toBeInTheDocument();
    await act(async () => {
      FakeSocket.last!.emit({ type: 'complete' });
      await Promise.resolve();
    });
    expect(screen.getByText('Send')).toBeInTheDocument();
    expect(input.disabled).toBe(false);
  });

  it('shows a transport error and drops the empty bubble', async () => {
    render(<ChatWidget agentId="a1" userId="u1" baseUrl="http://x" fetch={fetchOk} webSocket={FakeSocket as unknown as typeof WebSocket} />);
    await screen.findByText('earlier');
    fireEvent.change(screen.getByLabelText('message'), { target: { value: 'q' } });
    fireEvent.click(screen.getByText('Send'));
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      FakeSocket.last!.emit({ type: 'error', error_message: 'quota exceeded' });
      await Promise.resolve();
    });
    expect(screen.getByText('quota exceeded')).toBeInTheDocument();
    expect(screen.getAllByText(/./).filter((el) => el.className.includes('nx-chat__msg--assistant'))).toHaveLength(1);
  });

  it('reports a failed history load', async () => {
    const failing = (async () => ({ ok: false, status: 500 })) as unknown as typeof fetch;
    render(<ChatWidget agentId="a1" userId="u1" baseUrl="http://x" fetch={failing} webSocket={FakeSocket as unknown as typeof WebSocket} />);
    expect(await screen.findByText('chat-history: HTTP 500')).toBeInTheDocument();
  });
});
