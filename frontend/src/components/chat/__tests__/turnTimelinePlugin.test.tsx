/**
 * @file_name: turnTimelinePlugin.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: A timeline event type registered by a plugin renders inside the turn rail; an unknown type still renders nothing.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { TurnTimeline } from '../TurnTimeline';
import { TIMELINE_EVENTS } from '@/platform/registries';
import type { TurnEvent } from '@/types';

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('plugin timeline events', () => {
  it('renders a registered type and ignores an unregistered one', () => {
    disposers.push(TIMELINE_EVENTS.register('acme_tick', { component: ({ event }) => <div data-testid="tick">tick {event.id}</div> }, { owner: 'acme.t' }));
    const events = [
      { id: 'e1', type: 'acme_tick' },
      { id: 'e2', type: 'acme_unknown' },
    ] as unknown as TurnEvent[];
    render(<TurnTimeline events={events} />);
    expect(screen.getByTestId('tick')).toHaveTextContent('tick e1');
    expect(screen.queryByText(/e2/)).toBeNull();
  });
});
