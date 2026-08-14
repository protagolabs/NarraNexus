/**
 * @file_name: rehypeMentions.ts
 * @author: NarraNexus
 * @date: 2026-08-14
 * @description: Highlighting @mentions without touching code.
 *
 * An agent @mentioning a teammate IS the handoff, so the addressee has to be
 * able to spot it without reading every message. The first version did that by
 * rewriting the markdown SOURCE — a `String.replace` over the whole body before
 * it was parsed.
 *
 * That put a literal `<span data-testid="mention-all" class="…">@all</span>`
 * inside every code block containing `@all`, `@everyone` or a teammate's name —
 * and a team room's main output is code and commands, where `@all` is a real
 * make target, a shell argument, and another IM's mention syntax. Markdown
 * escapes HTML inside a fence, so it was never a security problem; it was worse
 * in a mundane way: what the user copied out of the room was broken, and the
 * first suspicion would fall on the model rather than on the renderer.
 *
 * A regex cannot know what a code block is. The AST does, so the replacement
 * happens there: walk the text nodes, skip anything under `<code>` or `<pre>`.
 *
 * The character class is `@` plus word/CJK characters and must stay identical to
 * `message_bus_trigger._extract_team_mentions` and to the composer's autocomplete
 * — highlighting someone who will not actually be woken (or missing someone who
 * will) is worse than not highlighting at all.
 */

import { visit } from 'unist-util-visit';
import type { Node, Parent } from 'unist';

const MENTION = /@([\w一-鿿]+)/g;

interface HastText extends Node {
  type: 'text';
  value: string;
}

interface HastElement extends Parent {
  type: 'element';
  tagName: string;
  properties?: Record<string, unknown>;
}

/**
 * A rehype plugin that wraps team @mentions in a highlight span.
 *
 * @param names - Lowercased member display names. `@all` / `@everyone` are always
 *   recognised; any other word must be a real member, or an email address and a
 *   decorative `@` would light up and teach the reader to ignore the highlight.
 */
export function rehypeMentions(names: Set<string>) {
  return () => (tree: Node) => {
    visit(
      tree,
      'text',
      (node: HastText, index: number | undefined, parent: Parent | undefined) => {
        if (!parent || index === undefined) return;
        // The whole point: the AST knows what is code and a regex does not.
        const tag = (parent as HastElement).tagName;
        if (tag === 'code' || tag === 'pre') return;

        const value = node.value;
        MENTION.lastIndex = 0;
        if (!MENTION.test(value)) return;
        MENTION.lastIndex = 0;

        const out: Node[] = [];
        let last = 0;
        let m: RegExpExecArray | null;
        while ((m = MENTION.exec(value)) !== null) {
          const word = m[1];
          const lower = word.toLowerCase();
          const isAll = lower === 'all' || lower === 'everyone';
          if (!isAll && !names.has(lower)) continue;
          if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) } as HastText);
          out.push({
            type: 'element',
            tagName: 'span',
            properties: {
              'data-testid': `mention-${word}`,
              className:
                'rounded px-0.5 font-medium text-[var(--color-carbon)] bg-[var(--nm-paper-warm)]',
            },
            children: [{ type: 'text', value: m[0] } as HastText],
          } as HastElement);
          last = m.index + m[0].length;
        }
        if (!out.length) return;
        if (last < value.length) {
          out.push({ type: 'text', value: value.slice(last) } as HastText);
        }
        parent.children.splice(index, 1, ...out);
        // Skip the nodes just inserted, so the walk does not re-enter them.
        return index + out.length;
      },
    );
  };
}
