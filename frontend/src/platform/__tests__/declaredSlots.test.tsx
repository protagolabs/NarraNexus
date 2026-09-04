/**
 * @file_name: declaredSlots.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Manifest-declared slots, renderers, timeline events and conversation kinds register gates before the plugin loads; a gate activates the plugin and defers to the real entry.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { registerActivation, resetActivation } from '@/platform/activation';
import { registerDeclaredUi, type FactoryPluginRow } from '@/platform/loader';
import { CHAT_HEADER_ACTIONS, COMPOSER_EXTENSIONS, CONVERSATION_KINDS, MESSAGE_RENDERERS, TIMELINE_EVENTS } from '@/platform/registries';

const ROW: FactoryPluginRow = {
  id: 'acme.slots',
  version: '1.0.0',
  enabled: true,
  loaded: true,
  state: 'enabled',
  frontend: {
    entry: 'plugin.js',
    ui: {
      conversationKinds: [{ id: 'acme.room', label: 'Room' }],
      messageRenderers: [{ id: 'acme.slots.card', contentPrefix: 'card:' }],
      timelineEvents: [{ id: 'acme.slots.tick', type: 'acme_tick' }],
      slots: [
        { id: 'acme.slots.strip', point: 'composerExtensions', when: ['conversationKind:chat'], order: 7 },
        { id: 'acme.slots.act', point: 'chatHeaderActions', label: 'Do it' },
      ],
    },
  },
};

describe('declared slot points', () => {
  it('registers gates and derives activation events', async () => {
    resetActivation();
    const events = registerDeclaredUi(ROW);
    expect(events).toEqual(expect.arrayContaining(['onRenderer:acme.slots.card', 'onTimelineEvent:acme.slots.tick', 'onSlot:acme.slots.strip', 'onSlot:acme.slots.act']));
    expect(CONVERSATION_KINDS.get('acme.room')?.labelKey).toBe('Room');
    expect(COMPOSER_EXTENSIONS.list().find((e) => e.id === 'acme.slots.strip')?.value).toMatchObject({ when: ['conversationKind:chat'], order: 7 });
    expect(TIMELINE_EVENTS.has('acme_tick')).toBe(true);
    const renderer = MESSAGE_RENDERERS.get('acme.slots.card')!;
    expect(renderer.match({ role: 'assistant', content: 'card:1' })).toBe(true);
    expect(renderer.match({ role: 'assistant', content: 'plain' })).toBe(false);

    // The action gate activates the plugin, which registers the real action under the same id; the gate then runs it.
    const ran: string[] = [];
    registerActivation(ROW.id, events, async () => {
      CHAT_HEADER_ACTIONS.register('acme.slots.act', { label: 'Do it', run: () => { ran.push('real'); } }, { owner: ROW.id, replace: true });
    });
    await CHAT_HEADER_ACTIONS.get('acme.slots.act').run({ agentId: 'a1' });
    expect(ran).toEqual(['real']);
  });

  it('a component slot gate renders nothing until the plugin is active, then the real component', async () => {
    resetActivation();
    registerDeclaredUi({ ...ROW, id: 'acme.slots2', frontend: { entry: 'p.js', ui: { slots: [{ id: 'acme.slots2.strip', point: 'composerExtensions' }] } } });
    const Gate = COMPOSER_EXTENSIONS.get('acme.slots2.strip').component;
    registerActivation('acme.slots2', ['onSlot:acme.slots2.strip'], async () => {
      COMPOSER_EXTENSIONS.register('acme.slots2.strip', { component: () => <b>real strip</b> }, { owner: 'acme.slots2', replace: true });
    });
    render(<Gate agentId="a1" />);
    expect(await screen.findByText('real strip')).toBeInTheDocument();
  });
});
