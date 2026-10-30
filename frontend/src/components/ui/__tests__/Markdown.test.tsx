/**
 * @file_name: Markdown.test.tsx
 * @description: Security + rendering contract of the shared Markdown renderer.
 *
 * Every test renders through the REAL remark/rehype pipeline into jsdom; nothing
 * in the pipeline is mocked. Markdown bodies come from places the viewer does
 * not control (agent replies, teammates, external IM via the inbox, Team intros
 * from a bundle), and this component runs on the app origin, so whatever raw
 * HTML reaches the DOM runs with the viewer's session. React 19 additionally
 * hoists `<script async>`, `<base>` and friends into document.head, which is why
 * these tests look at the whole document and not just the rendered container.
 */
import { afterEach, describe, expect, test } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import type { Node, Parent } from 'unist';
import { Markdown } from '../Markdown';
import { rehypeMentions } from '@/components/chat/team/rehypeMentions';

const headMarkup = () => document.head.innerHTML;

function draw(content: string) {
  return render(<Markdown content={content} />).container;
}

afterEach(() => {
  cleanup();
  document.head.querySelectorAll('[data-rtl-leak], script, base, link, meta[http-equiv]').forEach((n) => n.remove());
});

describe('Markdown: hostile raw HTML never reaches the DOM', () => {
  test.each([
    ['iframe srcdoc', '<iframe srcdoc="<script>parent.pwned=1</script>"></iframe>', 'iframe'],
    ['iframe src', '<iframe src="https://evil.example/"></iframe>', 'iframe'],
    ['async script', '<script async src="https://evil.example/x.js"></script>', 'script'],
    ['inline script', '<script>window.pwned = 1</script>', 'script'],
    ['base', '<base href="https://evil.example/">', 'base'],
    ['style', '<style>body { display: none }</style>', 'style'],
    ['form', '<form action="https://evil.example/"><button>go</button></form>', 'form'],
    ['embed', '<embed src="https://evil.example/x.swf">', 'embed'],
    ['object', '<object data="https://evil.example/x.html"></object>', 'object'],
    ['link stylesheet', '<link rel="stylesheet" href="https://evil.example/x.css">', 'link'],
    ['meta refresh', '<meta http-equiv="refresh" content="0;url=https://evil.example/">', 'meta[http-equiv]'],
    ['svg', '<svg onload="window.pwned=1"><script>window.pwned=1</script></svg>', 'svg'],
    ['math', '<math><mtext></mtext></math>', 'math'],
    ['video', '<video src="https://evil.example/x.mp4" autoplay></video>', 'video'],
    ['template', '<template><script>window.pwned=1</script></template>', 'template'],
  ])('%s', (_name, payload, selector) => {
    const before = headMarkup();
    const container = draw(`hello\n\n${payload}\n\nworld`);
    expect(document.querySelector(selector)).toBeNull();
    expect(container.textContent).toContain('hello');
    expect(container.textContent).toContain('world');
    expect(headMarkup()).toBe(before);
  });

  test('the text of a stripped style or script is not shown as prose', () => {
    const container = draw('<style>body { display: none }</style>\n\n<script>var leaked = 1</script>\n\nok');
    expect(container.textContent).not.toContain('display');
    expect(container.textContent).not.toContain('leaked');
  });

  test('event handlers are dropped and the benign element survives', () => {
    const container = draw('<img src="https://cdn.example/a.png" onerror="window.pwned=1" onload="window.pwned=1">');
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('https://cdn.example/a.png');
    expect(img?.hasAttribute('onerror')).toBe(false);
    expect(img?.hasAttribute('onload')).toBe(false);
  });

  test.each([
    ['plain', '<a href="javascript:alert(1)">x</a>'],
    ['entity encoded', '<a href="&#106;avascript:alert(1)">x</a>'],
    ['data html', '<a href="data:text/html,<script>alert(1)</script>">x</a>'],
    ['vbscript', '<a href="vbscript:msgbox(1)">x</a>'],
  ])('a %s link loses its href', (_name, payload) => {
    const a = draw(payload).querySelector('a');
    expect(a?.getAttribute('href') ?? '').toBe('');
  });

  test('inline style and arbitrary classes are dropped, so raw HTML cannot draw over the app', () => {
    const container = draw(
      '<div style="position:fixed;inset:0" class="fixed inset-0 z-50">overlay</div>',
    );
    const div = container.querySelector('.markdown-content > div');
    expect(div?.textContent).toBe('overlay');
    expect(div?.hasAttribute('style')).toBe(false);
    expect(div?.hasAttribute('class')).toBe(false);
  });

  test('a raw id or name cannot clobber document globals', () => {
    const container = draw('<a id="getElementById" name="cookie" href="https://x.example/">x</a>');
    const a = container.querySelector('a');
    expect(a?.id).toBe('user-content-getElementById');
    expect(a?.getAttribute('name')).toBe('user-content-cookie');
    expect(typeof document.getElementById).toBe('function');
  });
});

describe('Markdown: link hardening', () => {
  test('a raw rel="opener" cannot defeat noopener on an external link', () => {
    const a = draw('<a href="https://x.example/" rel="opener" target="_self">x</a>').querySelector('a');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noopener noreferrer');
  });

  test('the link component itself wins over any props a later plugin leaves on the node', () => {
    // A caller's rehype plugin runs AFTER the sanitizer, so this is the only
    // route by which rel/target can still reach the component. It pins the
    // prop order independently of what the schema happens to allow.
    const injectOpener = () => (tree: Node) => {
      const walk = (n: Node) => {
        const el = n as Parent & { tagName?: string; properties?: Record<string, unknown> };
        if (el.tagName === 'a') el.properties = { ...el.properties, rel: 'opener', target: '_self' };
        (el.children ?? []).forEach(walk);
      };
      walk(tree);
    };
    const { container } = render(
      <Markdown content="[x](https://x.example/)" rehypePlugins={[injectOpener]} />,
    );
    const a = container.querySelector('a');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noopener noreferrer');
  });

  test('an internal link gets neither target nor rel, and no raw rel slips through', () => {
    const a = draw('<a href="/app/agents" rel="opener" target="_blank">x</a>').querySelector('a');
    expect(a?.getAttribute('href')).toBe('/app/agents');
    expect(a?.hasAttribute('target')).toBe(false);
    expect(a?.hasAttribute('rel')).toBe(false);
  });
});

describe('Markdown: what the app legitimately renders survives', () => {
  test('plain inline and block HTML', () => {
    const container = draw(
      '<b>bold</b> <i>it</i> <kbd>Ctrl</kbd> H<sub>2</sub>O x<sup>2</sup><br><mark>m</mark>\n\n<blockquote>q</blockquote>\n\n<hr>',
    );
    for (const tag of ['b', 'i', 'kbd', 'sub', 'sup', 'br', 'blockquote', 'hr']) {
      expect(container.querySelector(tag), tag).not.toBeNull();
    }
    expect(container.textContent).toContain('bold');
  });

  test('details and summary, including the open state', () => {
    const container = draw('<details open><summary>more</summary>\n\nhidden **text**\n\n</details>');
    const details = container.querySelector('details');
    expect(details?.hasAttribute('open')).toBe(true);
    expect(details?.querySelector('summary')?.textContent).toBe('more');
    expect(details?.querySelector('strong')?.textContent).toBe('text');
  });

  test('GFM table, strikethrough, autolink and task list', () => {
    const container = draw(
      '| a | b |\n|---|:-:|\n| 1 | 2 |\n\n~~gone~~ https://x.example/p\n\n- [x] done\n- [ ] todo\n',
    );
    expect(container.querySelector('table th')?.textContent).toBe('a');
    expect(container.querySelector('table td')?.textContent).toBe('1');
    expect(container.querySelector('del')?.textContent).toBe('gone');
    const a = container.querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://x.example/p');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noopener noreferrer');
    const boxes = container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    expect(boxes).toHaveLength(2);
    expect(boxes[0].checked).toBe(true);
    expect(boxes[1].checked).toBe(false);
    expect(boxes[0].disabled).toBe(true);
    expect(container.querySelector('li.task-list-item')).not.toBeNull();
    expect(container.querySelector('ul.contains-task-list')).not.toBeNull();
  });

  test('fenced code keeps its language class and its literal HTML', () => {
    const container = draw('```python\nprint("<script>x</script>")\n```\n\nand `inline`');
    const code = container.querySelector('pre code');
    expect(code?.className).toContain('language-python');
    expect(code?.textContent).toContain('<script>x</script>');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('code.inline-code')?.textContent).toBe('inline');
  });

  test('a footnote reference still points at its note', () => {
    const container = draw('claim[^1]\n\n[^1]: the source\n');
    const ref = container.querySelector<HTMLAnchorElement>('a[data-footnote-ref]');
    const href = ref?.getAttribute('href') ?? '';
    expect(href.startsWith('#')).toBe(true);
    const note = container.querySelector(`[id="${href.slice(1)}"]`);
    expect(note?.textContent).toContain('the source');
    const back = container.querySelector<HTMLAnchorElement>('a[data-footnote-backref]');
    expect(container.querySelector(`[id="${(back?.getAttribute('href') ?? '').slice(1)}"]`)).toBe(ref);
  });

  test('an in-page link lands on the prefixed id of a raw anchor', () => {
    const container = draw('<a id="top">anchor</a>\n\n[back](#top)');
    const link = Array.from(container.querySelectorAll('a')).find((a) => a.textContent === 'back');
    expect(container.querySelector(`[id="${(link?.getAttribute('href') ?? '').slice(1)}"]`)?.textContent).toBe('anchor');
  });

  test('a caller plugin runs after the sanitizer, so mention spans keep their markup', () => {
    const plugins = [rehypeMentions(new Set(['bruno']))];
    const { container } = render(<Markdown content="@Bruno and @all" rehypePlugins={plugins} />);
    const span = container.querySelector('[data-testid="mention-Bruno"]');
    expect(span?.textContent).toBe('@Bruno');
    expect(span?.className).toContain('rounded');
    expect(container.querySelector('[data-testid="mention-all"]')).not.toBeNull();
  });
});
