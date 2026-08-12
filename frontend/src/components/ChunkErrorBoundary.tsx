/**
 * @file_name: ChunkErrorBoundary.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: Route-level error boundary that turns a crashed render into a
 * "refresh" prompt instead of a blank white screen.
 *
 * The most common trigger is a stale lazy-chunk 404 after a deploy: the dynamic
 * import throws "Failed to fetch dynamically imported module", Suspense surfaces
 * it to the nearest boundary, and with no boundary the whole React tree unmounts
 * to a white page (the bug this fixes). `chunkReload` auto-reloads once for the
 * preloadError event path; this boundary is the backstop for anything that
 * still reaches render — a second deploy in one session, or any other render
 * crash — where a visible recovery affordance beats a blank screen.
 *
 * Deliberately dependency-light: styles are inline and copy is plain English so
 * the fallback renders even if app CSS / i18n resources are part of what failed.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep a console trace; the user-facing UI stays a simple refresh prompt.
    console.error('[ChunkErrorBoundary] render crashed', error, info);
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '1rem',
          padding: '2rem',
          textAlign: 'center',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>
          A new version is available
        </h1>
        <p style={{ maxWidth: '28rem', opacity: 0.7 }}>
          The page couldn’t finish loading — this usually means the app was just
          updated. Refresh to load the latest version.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            padding: '0.5rem 1.25rem',
            borderRadius: '8px',
            border: '1px solid currentColor',
            cursor: 'pointer',
            background: 'transparent',
            font: 'inherit',
          }}
        >
          Refresh
        </button>
      </div>
    );
  }
}
