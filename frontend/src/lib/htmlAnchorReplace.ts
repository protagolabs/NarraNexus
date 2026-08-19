/**
 * @file_name: htmlAnchorReplace.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: Anchored literal replace for html per-element editing
 * (spec A §3.2). The bridge inside the artifact iframe reports an element
 * edit as {innerBefore, innerAfter, outerBefore}; this maps it back onto the
 * SOURCE text. The anchor must locate uniquely:
 *
 *   inner unique   → replace it
 *   inner absent   → JS-generated content, refuse ("not-found" → AI channel)
 *   inner ambiguous→ widen to the element's outerHTML; unique → replace the
 *                    inner within that one occurrence; still ambiguous →
 *                    refuse ("ambiguous")
 *
 * NEVER a nearest-guess replacement — an edit landing on the wrong copy of
 * the text is strictly worse than a refusal.
 */

export interface BridgeEdit {
  innerBefore: string;
  innerAfter: string;
  outerBefore: string;
}

export type BridgeEditResult =
  | { ok: true; result: string }
  | { ok: false; reason: 'not-found' | 'ambiguous' | 'no-change' };

function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let idx = haystack.indexOf(needle);
  while (idx !== -1) {
    count += 1;
    idx = haystack.indexOf(needle, idx + 1);
  }
  return count;
}

export function applyBridgeEdit(source: string, edit: BridgeEdit): BridgeEditResult {
  const { innerBefore, innerAfter, outerBefore } = edit;
  if (innerAfter === innerBefore) return { ok: false, reason: 'no-change' };

  const innerCount = countOccurrences(source, innerBefore);
  if (innerCount === 1) {
    const idx = source.indexOf(innerBefore);
    return {
      ok: true,
      result: source.slice(0, idx) + innerAfter + source.slice(idx + innerBefore.length),
    };
  }
  if (innerCount === 0) return { ok: false, reason: 'not-found' };

  // Inner text ambiguous — widen to the element's outerHTML.
  const outerCount = countOccurrences(source, outerBefore);
  if (outerCount !== 1) return { ok: false, reason: 'ambiguous' };

  // Replace the inner within the outer occurrence. Search after the opening
  // tag's '>' so an attribute that happens to contain the same text (e.g.
  // title="repeated") can't be hit.
  const tagEnd = outerBefore.indexOf('>');
  const innerIdxInOuter = outerBefore.indexOf(innerBefore, tagEnd + 1);
  if (innerIdxInOuter === -1) return { ok: false, reason: 'not-found' };
  const newOuter =
    outerBefore.slice(0, innerIdxInOuter) +
    innerAfter +
    outerBefore.slice(innerIdxInOuter + innerBefore.length);

  const outerIdx = source.indexOf(outerBefore);
  return {
    ok: true,
    result: source.slice(0, outerIdx) + newOuter + source.slice(outerIdx + outerBefore.length),
  };
}
