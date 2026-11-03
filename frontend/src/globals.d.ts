/// <reference types="vite/client" />

// Injected at build time by vite.config.ts from package.json's version field.
// See vite.config.ts `define` and Sidebar.tsx footer.
declare const __APP_VERSION__: string;

// Unique per build; the same id is emitted as /build.json so an open cloud tab
// can tell the deployment moved on (vite.config.ts, src/lib/buildWatch/).
declare const __APP_BUILD_ID__: string;
