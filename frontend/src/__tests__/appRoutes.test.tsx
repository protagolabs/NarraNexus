/**
 * @file_name: appRoutes.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The shell's route table really is built from the PAGES registry — delete the wiring in App.tsx and this goes red.
 *
 * `pageRoutes.test.tsx` proves the builder; `builtin.test.ts` proves the
 * registry's content. This closes the last link: `AppRoutes` (what `App`
 * renders) consumes `PAGES`, for the top-level routes and for the children
 * of the protected /app layout.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { Suspense } from 'react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '@/App';
import { PAGES } from '@/platform/registries';

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="location">{loc.pathname + loc.search}</output>;
}

function mount(at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Suspense fallback={<p>loading</p>}>
        <AppRoutes />
      </Suspense>
      <LocationProbe />
    </MemoryRouter>,
  );
}

const disposers: Array<() => void> = [];
afterEach(() => {
  while (disposers.length) disposers.pop()!();
});

describe('AppRoutes', () => {
  it('renders a builtin top-level page from the registry (/nm-playground is open, no guard)', async () => {
    mount('/nm-playground');
    expect(await screen.findByText('NM Design System Playground')).toBeInTheDocument();
  });

  it('a top-level page registered after mount is routable without remounting', async () => {
    mount('/late');
    // Nothing registered at /late: the catch-all sends the visitor to `/`.
    expect(screen.getByTestId('location')).not.toHaveTextContent('/late');
    act(() => {
      disposers.push(
        PAGES.register('late', { path: '/late', element: () => <p>late page</p>, guard: 'open', layout: 'top' }, { owner: 'acme.plugin' }),
      );
    });
    // Re-enter the path now that it exists.
    mount('/late');
    expect(await screen.findByText('late page')).toBeInTheDocument();
  });

  it('a page registered under /app is a child of the protected layout', async () => {
    disposers.push(
      PAGES.register('probe', { path: 'probe', element: () => <p>probe</p>, guard: 'protected', layout: 'app' }, { owner: 'acme.plugin' }),
    );
    mount('/app/probe');
    // Logged out: the layout's ProtectedRoute bounces to /login and keeps the
    // deep link as ?next=. An unregistered /app child would not match the
    // layout at all and fall to the catch-all (no ?next=).
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/login?next=%2Fapp%2Fprobe'));
  });

  it('an unregistered /app child falls through to the catch-all (no deep link kept)', async () => {
    mount('/app/nope');
    await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('/app/nope'));
    expect(screen.getByTestId('location')).not.toHaveTextContent('next=');
  });
});
