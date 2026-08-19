/**
 * @file_name: htmlAnchorReplace.test.ts
 * @description: The anchored literal replace behind html per-element editing
 * (spec A §3.2): the anchor is the element's ORIGINAL innerHTML; it must
 * match the source exactly once. Zero matches = JS-generated content
 * (degrade to AI); multiple matches = widen to the element's outerHTML and
 * replace inside that unique occurrence; still ambiguous = degrade. NEVER
 * a nearest-guess replacement — a wrong-spot edit is worse than a refusal.
 */

import { describe, expect, it } from 'vitest';
import { applyBridgeEdit } from '../htmlAnchorReplace';

const PAGE = [
  '<html><body>',
  '<h1>Quarterly Report</h1>',
  '<p class="intro">Welcome</p>',
  '<p class="a">repeated</p>',
  '<p class="b">repeated</p>',
  '<script>document.title = "x"</script>',
  '</body></html>',
].join('\n');

describe('applyBridgeEdit', () => {
  it('replaces a unique inner anchor', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Quarterly Report',
      innerAfter: 'Q3 Report',
      outerBefore: '<h1>Quarterly Report</h1>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.result).toContain('<h1>Q3 Report</h1>');
      expect(r.result).not.toContain('Quarterly Report');
    }
  });

  it('widens to the outer anchor when the inner text is ambiguous', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'repeated',
      innerAfter: 'edited once',
      outerBefore: '<p class="a">repeated</p>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.result).toContain('<p class="a">edited once</p>');
      expect(r.result).toContain('<p class="b">repeated</p>'); // untouched
    }
  });

  it('refuses when the inner anchor is not in the source (JS-generated)', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'rendered by javascript',
      innerAfter: 'changed',
      outerBefore: '<div>rendered by javascript</div>',
    });
    expect(r).toEqual({ ok: false, reason: 'not-found' });
  });

  it('refuses when both anchors are ambiguous', () => {
    const page = '<p>x</p><p>x</p>';
    const r = applyBridgeEdit(page, {
      innerBefore: 'x',
      innerAfter: 'y',
      outerBefore: '<p>x</p>',
    });
    expect(r).toEqual({ ok: false, reason: 'ambiguous' });
  });

  it('replacement may contain <br> and inline format tags', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Welcome',
      innerAfter: 'Welcome<br><strong>back</strong>',
      outerBefore: '<p class="intro">Welcome</p>',
    });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.result).toContain('<p class="intro">Welcome<br><strong>back</strong></p>');
  });

  it('no-op edits are refused (nothing to save)', () => {
    const r = applyBridgeEdit(PAGE, {
      innerBefore: 'Welcome',
      innerAfter: 'Welcome',
      outerBefore: '<p class="intro">Welcome</p>',
    });
    expect(r).toEqual({ ok: false, reason: 'no-change' });
  });
});
