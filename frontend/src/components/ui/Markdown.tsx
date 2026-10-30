/**
 * Markdown renderer component
 * Renders markdown content with syntax highlighting and GFM support
 *
 * SECURITY: bodies come from sources the viewer does not control (agent replies,
 * teammates, external IM through the inbox, Team intros from a bundle) and this
 * renders on the app origin. Raw HTML is therefore parsed (rehypeRaw) and then
 * sanitized; nothing may run between the two, and a caller's plugin only ever
 * runs after the sanitizer.
 */

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';
import { cn } from '@/lib/utils';

// `strip` drops an element WITH its contents. Anything else outside the schema
// is unwrapped and its text stays, which would print a dropped <style> sheet
// into the message as prose.
const SANITIZE_SCHEMA = {
  ...defaultSchema,
  strip: [...(defaultSchema.strip ?? []), 'style'],
};

// The sanitizer prefixes every id (DOM clobbering), so it owns the prefix:
// remark-rehype must not add its own to footnote ids or they get it twice, and
// an in-page `#x` link has to aim at the prefixed id to land anywhere.
const ID_PREFIX = defaultSchema.clobberPrefix ?? '';
const REMARK_REHYPE_OPTIONS = { clobberPrefix: '' };

interface MarkdownProps {
  content: string;
  className?: string;
  compact?: boolean;
  /**
   * Extra rehype plugins, appended after `rehypeRaw` and the sanitizer, so
   * what a plugin adds (the mention span's class and test id) is not stripped.
   * A plugin therefore runs on trusted-by-then output and must not copy raw
   * content back into properties.
   *
   * Exists so a caller can transform the rendered tree instead of rewriting the
   * markdown SOURCE. The team room learned the difference the hard way: a
   * regex over the source cannot tell prose from a code block, so highlighting
   * @mentions put literal `<span …>` markup inside every fence that mentioned
   * `@all`. The AST knows what code is.
   *
   * Pass a STABLE array (module constant or `useMemo`) — this component is
   * `memo`'d on shallow equality, and a fresh array each render would re-parse
   * the whole body on every keystroke of a streaming reply.
   */
  rehypePlugins?: PluggableList;
}

// memo matters here: remark/rehype re-parse the whole string on every
// render, and chat surfaces re-render on every WebSocket delta. All props
// are primitives, so shallow equality skips the re-parse for content that
// didn't change (e.g. earlier segments while the last one streams).
function isExternalHref(href: string | undefined): boolean {
  if (!href || href.startsWith('#')) return false;
  try {
    return new URL(href, window.location.href).origin !== window.location.origin;
  } catch {
    return false;
  }
}

export const Markdown = memo(function Markdown({
  content,
  className,
  compact = false,
  rehypePlugins,
}: MarkdownProps) {
  return (
    <div className={cn(
      'markdown-content',
      compact && 'markdown-compact',
      className
    )}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        remarkRehypeOptions={REMARK_REHYPE_OPTIONS}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, SANITIZE_SCHEMA], ...(rehypePlugins ?? [])]}
        components={{
          // Custom link handling - open external links in new tab. {...props}
          // comes first: target/rel are computed here and must win, or a link
          // carrying rel="opener" hands the new tab window.opener (tabnabbing).
          a: ({ href, children, ...props }) => {
            // Resolved, not string-matched: //evil.example and HTTP://evil.example
            // are external too, and would otherwise replace the app in this tab.
            const isExternal = isExternalHref(href);
            return (
              <a
                {...props}
                href={href?.startsWith('#') ? `#${ID_PREFIX}${href.slice(1)}` : href}
                target={isExternal ? '_blank' : undefined}
                rel={isExternal ? 'noopener noreferrer' : undefined}
              >
                {children}
              </a>
            );
          },
          // Add copy button to code blocks
          pre: ({ children, ...props }) => (
            <pre className="group relative" {...props}>
              {children}
            </pre>
          ),
          // Style inline code
          code: ({ className: codeClassName, children, ...props }) => {
            const isInline = !codeClassName;
            return (
              <code
                className={cn(
                  isInline && 'inline-code',
                  codeClassName
                )}
                {...props}
              >
                {children}
              </code>
            );
          },
          // Style tables
          table: ({ children, ...props }) => (
            <div className="overflow-x-auto">
              <table {...props}>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

// Compact version for message previews
export function MarkdownPreview({ content, maxLength = 200 }: { content: string; maxLength?: number }) {
  const truncated = content.length > maxLength
    ? content.slice(0, maxLength) + '...'
    : content;

  return (
    <Markdown
      content={truncated}
      compact
      className="text-[var(--text-secondary)] line-clamp-2"
    />
  );
}
