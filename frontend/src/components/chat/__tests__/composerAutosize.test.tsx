/**
 * The composer grows with its content (height follows scrollHeight; the CSS
 * max-height caps it, past which the textarea scrolls internally) and
 * shrinks back when text is removed.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { Composer } from '../Composer';

function renderComposer() {
  render(
    <Composer
      agentId="a1"
      placeholder="type"
      disabled={false}
      onSubmit={vi.fn()}
      onEmptyChange={vi.fn()}
    />,
  );
  const textarea = screen.getByPlaceholderText('type') as HTMLTextAreaElement;
  // jsdom has no layout — drive scrollHeight by line count so the effect
  // has something real to read.
  Object.defineProperty(textarea, 'scrollHeight', {
    get: () => 52 + (textarea.value.split('\n').length - 1) * 24,
  });
  return textarea;
}

describe('composer autosize', () => {
  it('height follows content and shrinks back', () => {
    const textarea = renderComposer();
    fireEvent.change(textarea, { target: { value: 'one\ntwo\nthree\nfour' } });
    expect(textarea.style.height).toBe(`${52 + 3 * 24}px`);
    fireEvent.change(textarea, { target: { value: 'one' } });
    expect(textarea.style.height).toBe('52px');
  });
});
