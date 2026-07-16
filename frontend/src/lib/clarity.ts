/**
 * @file_name: clarity.ts
 * @description: Load the Microsoft Clarity tracking snippet, cloud-only.
 *
 * Clarity is a hosted analytics SaaS (heatmaps + session recordings) — we
 * run none of its backend, we just need the official bootstrap snippet
 * present on cloud pages. Desktop (Tauri) and local self-host builds must
 * never load it: `isForcedCloud()` is false there, so `initClarity()` is a
 * no-op and no request to clarity.ms is ever made.
 *
 * The project id is not a secret — Clarity's own snippet design puts it in
 * plaintext in the page source — so it is compiled in directly rather than
 * routed through the deploy-pipeline-injected /config.js.
 */
import { isForcedCloud } from '@/lib/runtimeConfig';

const CLARITY_PROJECT_ID = 'xnaag1qmu0';
const MARKER_ATTR = 'data-clarity-project-id';

/**
 * Install the Microsoft Clarity tracking snippet. No-op outside forced-cloud
 * deploys. Safe to call more than once (HMR / StrictMode double-invoke) — the
 * marker attribute on the injected `<script>` prevents a second insertion.
 */
export function initClarity(): void {
  if (!isForcedCloud()) return;
  if (document.head.querySelector(`script[${MARKER_ATTR}]`)) return;

  const script = document.createElement('script');
  script.setAttribute(MARKER_ATTR, CLARITY_PROJECT_ID);
  script.text = `(function(c,l,a,r,i,t,y){
    c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
  })(window, document, "clarity", "script", "${CLARITY_PROJECT_ID}");`;
  document.head.appendChild(script);
}
