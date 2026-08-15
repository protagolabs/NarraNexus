/**
 * @file_name: mentionPattern.ts
 * @author: NarraNexus
 * @date: 2026-08-14
 * @description: What counts as an @mention, in one place.
 *
 * There are two renderers — one for plain text (the user's own messages, which
 * do not go through markdown) and one for the markdown AST (everything an agent
 * says). Both need the same answer, and they had a hand-copied regex each, in
 * the same folder, under a comment insisting the two must match.
 *
 * The pattern also has to match the SERVER's `_extract_team_mentions` and the
 * composer's autocomplete character for character. Highlighting someone who
 * will not actually be woken — or missing someone who will — is worse than not
 * highlighting at all, because it teaches the reader to distrust the highlight.
 * Keeping one literal here does not enforce that across the language boundary,
 * but it removes the copy that was drifting on this side of it.
 */

/** `@` followed by word characters or CJK. Global: callers drive it with exec. */
export const MENTION_PATTERN = /@([\w一-鿿]+)/g;

/** A fresh matcher — `lastIndex` is stateful, so a shared instance is a bug. */
export function mentionMatcher(): RegExp {
  return new RegExp(MENTION_PATTERN.source, 'g');
}

/**
 * The lowercased @tokens in a piece of text.
 *
 * The SEND path's entry point. It exists here rather than in the composer
 * because "what counts as an @token" is the one thing the send path and the two
 * renderers must never disagree about: the highlighted names and the woken
 * agents come from the same sentence, and a reader who sees three names lit up
 * and two teammates answer has no way to tell which half is wrong.
 *
 * What happens NEXT is deliberately not shared. The renderers ask "is this word
 * addressed to a member" (exact match, or it would light up an email address);
 * the send path resolves loosely — first names, prefixes — because a user typing
 * `@ana` for "Ana Silva" means her, and refusing to wake anyone is a worse
 * answer than waking her. Those are different questions about the same tokens,
 * and collapsing them would make one of the two wrong.
 */
export function mentionTokens(text: string): Set<string> {
  const out = new Set<string>();
  const re = mentionMatcher();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) out.add(m[1].toLowerCase());
  return out;
}

/**
 * Does this word address someone who will actually be reached?
 *
 * `@all` / `@everyone` always do. Anything else has to be a real member —
 * otherwise an email address and a decorative `@` light up too.
 */
export function isAddressed(word: string, names: Set<string>): boolean {
  const lower = word.toLowerCase();
  return lower === 'all' || lower === 'everyone' || names.has(lower);
}
