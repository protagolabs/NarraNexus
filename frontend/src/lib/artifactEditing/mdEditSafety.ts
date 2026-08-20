/**
 * @file_name: mdEditSafety.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: Loss guards for the md block editor (spec A §1.3, refined
 * after the 2026-08-19 Crepe round-trip spike).
 *
 * Byte-exact round-trip does not exist in the block-editor world: every
 * remark-based serializer normalizes style (bullet chars, table dashes,
 * emphasis markers). The guard is therefore SEMANTIC:
 *
 *  - frontmatter would be DESTROYED by the editor (parsed as a thematic
 *    break + heading), so it is split off before the editor sees the text
 *    and re-attached verbatim on save;
 *  - editability requires AST equivalence between the original body and the
 *    editor's own serialization of it — a parse that loses structure (html
 *    blocks, math, anything the editor can't represent) disables editing
 *    with a banner; pure style normalization passes and is applied on the
 *    first actual save (semantically lossless).
 */

import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';

/** Split a leading YAML frontmatter block off verbatim (no parsing).

    Line-ending aware (review #334 I7): a CRLF document's fence lines are
    `---\r\n`, and the LF-only version of this function classified the whole
    block as body — Crepe then destroyed it. The split preserves the
    ORIGINAL bytes (frontmatter + body reassemble exactly); the save-time
    line-ending policy lives in MarkdownRenderer, not here. */
export function extractFrontmatter(text: string): { frontmatter: string; body: string } {
  // Must be the very first line, `---` alone, closed by another `---` (or
  // `...`) alone. Anything else — including a fence that never closes — is
  // body text, not frontmatter.
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  if (!text.startsWith(`---${eol}`) && text !== '---') {
    return { frontmatter: '', body: text };
  }
  const lines = text.split(eol);
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line === '---' || line === '...') {
      const fm = lines.slice(0, i + 1).join(eol) + eol;
      const body = lines.slice(i + 1).join(eol);
      return { frontmatter: fm, body };
    }
    if (line.trim() === '') {
      // A blank line before the closing fence means CommonMark reads the
      // opener as a thematic break, not YAML — treat as body.
      return { frontmatter: '', body: text };
    }
  }
  return { frontmatter: '', body: text };
}

type UnknownNode = { type?: string; [key: string]: unknown };

/** Fields that carry style/position, not meaning. */
const IGNORED_KEYS = new Set(['position', 'spread', 'checked_style']);

function normalizeNode(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(normalizeNode);
  if (node && typeof node === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(node as UnknownNode)) {
      if (IGNORED_KEYS.has(k)) continue;
      out[k] = normalizeNode(v);
    }
    return out;
  }
  return node;
}

function parseTree(text: string): unknown {
  const tree = unified().use(remarkParse).use(remarkGfm).parse(text);
  return normalizeNode(tree);
}

/**
 * True when two markdown texts carry the same CONTENT — same mdast structure
 * and text, ignoring source positions and pure style choices.
 */
export function mdAstEqual(a: string, b: string): boolean {
  try {
    return JSON.stringify(parseTree(a)) === JSON.stringify(parseTree(b));
  } catch {
    // A parser crash means we cannot vouch for anything — not equivalent.
    return false;
  }
}
