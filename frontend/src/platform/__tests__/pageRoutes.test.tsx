/**
 * @file_name: pageRoutes.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The route table really is built from the PAGES registry, guards apply, and late registrations show up.
 */
import { act, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { pageRouteElements } from '@/platform/pageRoutes';
import { Registry, useRegistryEntries, type PageDef } from '@/platform/registries';

const Protected = ({ children }: { children: ReactNode }) => <div data-testid="protected">{children}</div>;
const Public = ({ children }: { children: ReactNode }) => <div data-testid="public">{children}</div>;
const wrappers = { ProtectedRoute: Protected, PublicRoute: Public };

function Host({ pages }: { pages: Registry<PageDef> }) {
  const routes = pageRouteElements(useRegistryEntries(pages), wrappers);
  return (
    <Routes>
      {routes.top}
      {routes.app}
    </Routes>
  );
}

describe('pageRouteElements', () => {
  it('renders a registered top-level page inside its guard wrapper', () => {
    const pages = new Registry<PageDef>('ui.pages');
    pages.register('hello', { path: '/hello', element: () => <p>hello page</p>, guard: 'public', layout: 'top' });
    render(<MemoryRouter initialEntries={['/hello']}><Host pages={pages} /></MemoryRouter>);
    expect(screen.getByText('hello page')).toBeInTheDocument();
    expect(screen.getByTestId('public')).toBeInTheDocument();
    expect(screen.queryByTestId('protected')).toBeNull();
  });

  it('an `open` page has no wrapper and a `protected` one is wrapped', () => {
    const pages = new Registry<PageDef>('ui.pages');
    pages.register('open', { path: '/open', element: () => <p>open page</p>, guard: 'open', layout: 'top' });
    pages.register('secret', { path: '/secret', element: () => <p>secret page</p>, guard: 'protected', layout: 'top' });
    const { unmount } = render(<MemoryRouter initialEntries={['/open']}><Host pages={pages} /></MemoryRouter>);
    expect(screen.getByText('open page')).toBeInTheDocument();
    expect(screen.queryByTestId('protected')).toBeNull();
    unmount();
    render(<MemoryRouter initialEntries={['/secret']}><Host pages={pages} /></MemoryRouter>);
    expect(screen.getByTestId('protected')).toHaveTextContent('secret page');
  });

  it('a page registered after first render appears without a remount', () => {
    const pages = new Registry<PageDef>('ui.pages');
    render(<MemoryRouter initialEntries={['/late']}><Host pages={pages} /></MemoryRouter>);
    expect(screen.queryByText('late page')).toBeNull();
    act(() => {
      pages.register('late', { path: '/late', element: () => <p>late page</p>, guard: 'open', layout: 'top' }, { owner: 'acme.plugin' });
    });
    expect(screen.getByText('late page')).toBeInTheDocument();
  });

  it('rejects an /app page that claims to be public', () => {
    const pages = new Registry<PageDef>('ui.pages');
    pages.register('leak', { path: 'leak', element: null, guard: 'public', layout: 'app' });
    expect(() => pageRouteElements(pages.list(), wrappers)).toThrow(/must declare guard "protected"/);
  });

  it('a `null` element renders an empty app route (placeholder for layout-owned views)', () => {
    const pages = new Registry<PageDef>('ui.pages');
    pages.register('chat', { path: 'chat', element: null, guard: 'protected', layout: 'app' });
    const { app } = pageRouteElements(pages.list(), wrappers);
    expect(app).toHaveLength(1);
    expect(app[0].props.element).toBeNull();
  });
});
