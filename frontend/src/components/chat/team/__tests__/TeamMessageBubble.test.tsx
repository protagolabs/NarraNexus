/**
 * @file_name: TeamMessageBubble.test.tsx
 * @description: What makes a six-member room readable.
 *
 * The acceptance criterion is blunt: in a six-member room you should be able to
 * tell who said what WITHOUT reading the names, and on mobile too. Today every
 * agent shares one silicon colour and one avatar style, and the only thing
 * separating two speakers is 10px of grey text that mobile hides entirely.
 *
 * The other three properties here each fix a way the room lies or tires:
 *
 *   - a long report eats the screen, so it collapses;
 *   - monologue and answer arrive concatenated, so they are laid out apart —
 *     but ONLY when the server actually recorded the boundary. A message with
 *     no segments renders as one block, which is what it did before, because
 *     guessing where the boundary was is worse than not showing one;
 *   - @mentions are invisible in the body, so the person being addressed has to
 *     read every message to find out it was them.
 */
import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

import { TeamMessageBubble, COLLAPSE_CHARS } from '../TeamMessageBubble';
import { senderIdentity } from '@/lib/senderIdentity';

function msg(over: Record<string, unknown> = {}) {
  return {
    message_id: 'm1',
    from_agent: 'agent_a',
    author_name: 'Ana',
    is_user: false,
    content: 'hello there',
    created_at: '2026-08-12T09:00:00Z',
    ...over,
  } as never;
}

function draw(over: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
  render(
    <TeamMessageBubble
      message={msg(over)}
      userLabel="Bin"
      leadAgentId=""
      memberNames={{ agent_a: 'Ana', agent_b: 'Bruno' }}
      {...props}
    />,
  );
}

describe('TeamMessageBubble', () => {
  // ── identity ──────────────────────────────────────────────────────────────

  test('an agent message carries its sender identity colour', () => {
    draw();
    const bubble = screen.getByTestId('bubble-m1');
    expect(bubble.className).toContain(senderIdentity('agent_a').accent);
  });

  test('two agents in the same room look different', () => {
    // The whole acceptance criterion in one assertion.
    expect(senderIdentity('agent_a').accent).not.toBe(senderIdentity('agent_b').accent);
  });

  test('the colour follows the agent id, not the display name', () => {
    draw({ author_name: 'Renamed' });
    expect(screen.getByTestId('bubble-m1').className).toContain(
      senderIdentity('agent_a').accent,
    );
  });

  test('the identity is on the avatar too, so mobile keeps it when names hide', () => {
    draw();
    expect(screen.getByTestId('avatar-m1').className).toContain(senderIdentity('agent_a').dot);
  });

  test('the user is not given an agent identity colour', () => {
    // The human is one party, always the same; hashing them into the agent
    // palette would make the room look like it has one more agent.
    draw({ is_user: true, from_agent: 'usr_1', author_name: 'Bin' });
    expect(screen.getByTestId('bubble-m1').className).not.toContain(
      senderIdentity('usr_1').accent,
    );
  });

  test('the lead is marked', () => {
    draw({}, { leadAgentId: 'agent_a' });
    expect(screen.getByTestId('lead-badge-m1')).toBeTruthy();
  });

  test('a non-lead has no badge', () => {
    draw({}, { leadAgentId: 'agent_b' });
    expect(screen.queryByTestId('lead-badge-m1')).toBeNull();
  });

  // ── long messages ─────────────────────────────────────────────────────────

  test('a long report collapses by default', () => {
    draw({ content: 'x'.repeat(COLLAPSE_CHARS + 50) });
    expect(screen.getByTestId('expand-m1')).toBeTruthy();
  });

  test('a short message has no expander', () => {
    draw({ content: 'short' });
    expect(screen.queryByTestId('expand-m1')).toBeNull();
  });

  test('expanding shows the rest and can be collapsed again', () => {
    const long = `${'x'.repeat(COLLAPSE_CHARS)}THEEND`;
    draw({ content: long });

    expect(screen.queryByText(/THEEND/)).toBeNull();
    fireEvent.click(screen.getByTestId('expand-m1'));
    expect(screen.getByText(/THEEND/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('expand-m1'));
    expect(screen.queryByText(/THEEND/)).toBeNull();
  });

  // ── monologue / reply ─────────────────────────────────────────────────────

  test('recorded segments render as distinct layers', () => {
    draw({
      content: 'thinkinganswering',
      segments: [
        { kind: 'monologue', text: 'thinking' },
        { kind: 'reply', text: 'answering' },
      ],
    });

    expect(screen.getByTestId('segment-monologue-0')).toBeTruthy();
    expect(screen.getByTestId('segment-reply-1')).toBeTruthy();
  });

  test('a message with no segments renders as one block', () => {
    // Every message written before the boundary was recorded. Guessing where it
    // was is worse than showing none — a wrong split presents deliberation as a
    // conclusion.
    draw({ content: 'one blob' });

    expect(screen.queryByTestId('segment-monologue-0')).toBeNull();
    expect(screen.getByText('one blob')).toBeTruthy();
  });

  test('an empty segment list is treated as no segments', () => {
    // `[]` reaches here from any path that recorded no boundary. Mapping over it
    // renders NOTHING — an empty array produces no children — so the message
    // body would silently vanish. The earlier version of this test only checked
    // that no segment testid existed, which an empty map satisfies too; it
    // survived the mutation that caused exactly this.
    draw({ content: 'one blob', segments: [] });

    expect(screen.queryByTestId('segment-reply-0')).toBeNull();
    expect(screen.getByText('one blob')).toBeTruthy();
    expect(screen.getByTestId('bubble-m1').textContent).toContain('one blob');
  });

  test('monologue is visually subordinate to the reply', () => {
    // The answer is what the room is for; the thinking is context. Rendering
    // them identically would make every agent look twice as loud.
    draw({
      content: 'ab',
      segments: [
        { kind: 'monologue', text: 'a' },
        { kind: 'reply', text: 'b' },
      ],
    });
    const mono = screen.getByTestId('segment-monologue-0');
    const reply = screen.getByTestId('segment-reply-1');
    expect(mono.className).not.toBe(reply.className);
  });

  // ── mentions ──────────────────────────────────────────────────────────────

  test('a mention of a member is highlighted', () => {
    draw({ content: 'hey @Bruno can you look' });
    expect(screen.getByTestId('mention-Bruno')).toBeTruthy();
  });

  test('a word that is not a member is not highlighted', () => {
    // Highlighting anything after an @ would light up email addresses and
    // decorations, and teach the reader to ignore the highlight.
    draw({ content: 'mail me at bin@example.com' });
    expect(screen.queryByTestId('mention-example')).toBeNull();
  });

  test('@all is highlighted', () => {
    draw({ content: '@all standup in 5' });
    expect(screen.getByTestId('mention-all')).toBeTruthy();
  });
});
