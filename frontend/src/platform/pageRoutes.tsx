/**
 * @file_name: pageRoutes.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Turns the PAGES registry into <Route> elements — the one place the route table is built.
 *
 * `App.tsx` renders the two arrays this returns (top-level routes and the
 * children of the /app layout). Keeping the builder out of `App.tsx` makes
 * it testable without mounting the whole shell: a test registers a page,
 * renders the routes in a MemoryRouter and sees the page. The guard
 * wrappers are injected because they live with the shell's auth state.
 *
 * An `/app` page that does not declare `guard: 'protected'` is dropped (not
 * thrown): this function runs inside `App.tsx`'s render body, above the
 * nearest `ChunkErrorBoundary`, so a throw here would white-screen the
 * whole shell over one bad manifest. `loader.registerDeclaredUi` already
 * rejects such entries at registration time (see loader.ts); this check
 * stays as defense in depth for anything that reaches PAGES another way
 * (a plugin's own bundle calling `host.registries.pages.register`).
 */
import type { ComponentType, ReactElement, ReactNode } from 'react';
import { Route } from 'react-router-dom';

import { reportUiError } from './errorSink';
import type { PageDef, RegistryEntry } from './registries';

export interface GuardWrappers {
  ProtectedRoute: ComponentType<{ children: ReactNode; skipWelcomeGate?: boolean }>;
  PublicRoute: ComponentType<{ children: ReactNode }>;
}

export interface PageRouteElements {
  /** Routes rendered at the top level, each wrapped per its `guard`. */
  top: ReactElement[];
  /** Children of the protected /app layout (no extra wrapper). */
  app: ReactElement[];
}

function pageElement(def: PageDef): ReactNode {
  return def.element ? <def.element /> : null;
}

function guarded(def: PageDef, wrappers: GuardWrappers): ReactNode {
  const inner = pageElement(def);
  if (def.guard === 'protected') {
    return <wrappers.ProtectedRoute skipWelcomeGate={def.skipWelcomeGate}>{inner}</wrappers.ProtectedRoute>;
  }
  if (def.guard === 'public') return <wrappers.PublicRoute>{inner}</wrappers.PublicRoute>;
  return inner;
}

export function pageRouteElements(entries: RegistryEntry<PageDef>[], wrappers: GuardWrappers): PageRouteElements {
  const top: ReactElement[] = [];
  const app: ReactElement[] = [];
  for (const { id, value, owner } of entries) {
    if (value.layout === 'top') {
      top.push(<Route key={id} path={value.path} element={guarded(value, wrappers)} />);
      continue;
    }
    if (value.guard !== 'protected') {
      const message = `ui.pages: "${id}" is under /app and must declare guard "protected" (got "${value.guard}")`;
      if (import.meta.env.DEV) console.error(message);
      reportUiError(new Error(message), { kind: 'render', source: owner, context: 'pageRouteElements' });
      continue;
    }
    app.push(<Route key={id} path={value.path} element={pageElement(value)} />);
  }
  return { top, app };
}
