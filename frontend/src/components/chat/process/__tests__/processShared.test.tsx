/**
 * ProcessEventRows — the shared terminal-style rows (thinking / tool
 * call / tool output) reused by ProcessPanel and the team roster's
 * member detail. Smoke test: each row type renders with the friendly
 * tool name.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProcessEventRows, PHASE_STEP_IDS, phaseSettled } from '../processShared';
import type { Step, TurnEvent } from '@/types';

function step(id: string, status: Step['status'] = 'running'): Step {
  return { id: `s-${id}`, step: id, title: `Step ${id}`, description: '', status, substeps: [], timestamp: 0 };
}

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: 'pondering' },
  {
    id: 'c1', ts: 2, type: 'tool_call', tool_name: 'mcp__x__read_file',
    tool_input: { path: '/tmp/a' }, pending: false,
  },
  {
    id: 'o1', ts: 3, type: 'tool_output', tool_name: 'mcp__x__read_file',
    output: '42 lines',
  },
];

describe('ProcessEventRows', () => {
  it('renders thinking, tool call (friendly name) and output rows', () => {
    render(<ProcessEventRows process={events} />);
    expect(screen.getByText('pondering')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.getByText('42 lines')).toBeInTheDocument();
  });
});

describe('PHASE_STEP_IDS (derived whitelist)', () => {
  it('is derived from PHASE_LABEL_KEYS and holds the top-level phases only', () => {
    for (const id of ['0', '1', '2', '2.5', '3', '3.4']) {
      expect(PHASE_STEP_IDS.has(id)).toBe(true);
    }
    // Sub-steps / echo / housekeeping are NOT phases.
    for (const id of ['3.4.1', '3.4.replay', '3.5', '4', '5']) {
      expect(PHASE_STEP_IDS.has(id)).toBe(false);
    }
  });
});

describe('phaseSettled', () => {
  // The shared settled rule MUST be fed the UNFILTERED steps: "a later phase
  // started" is how the run-agent (3.4) row settles via housekeeping ids
  // (4/5) that the phase whitelist drops. Passing the filtered list silently
  // breaks it — this is the bug the extraction (Minor 4) exists to prevent.
  it('settles a running phase when a later phase id is present in the full step list', () => {
    const full = [step('3.4', 'running'), step('4', 'running')];
    expect(phaseSettled(step('3.4', 'running'), full, false)).toBe(true);
  });

  it('does NOT settle when the caller passes only the whitelisted (filtered) steps', () => {
    // '4' filtered out → no later phase visible → 3.4 must stay running.
    const filtered = [step('3.4', 'running')];
    expect(phaseSettled(step('3.4', 'running'), filtered, false)).toBe(false);
  });

  it('own completed status settles regardless of what else is present', () => {
    expect(phaseSettled(step('3.4', 'completed'), [step('3.4', 'completed')], false)).toBe(true);
  });

  it('a pre-loop phase (step < 3.4) settles once process events have arrived', () => {
    expect(phaseSettled(step('3', 'running'), [step('3', 'running')], true)).toBe(true);
    // ...but the run-agent phase (3.4) is NOT settled by process events alone.
    expect(phaseSettled(step('3.4', 'running'), [step('3.4', 'running')], true)).toBe(false);
  });
});
