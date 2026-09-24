/**
 * @file_name: BrowserApprovalNotice.tsx
 * @date: 2026-09-22
 * @description: Surface pending site approvals wherever the user actually is.
 *
 * Mounted in the app shell, deliberately.
 *
 * The first version put the approval prompt inside the browser panel, which
 * only exists when a URL tab is open in stream mode. Users get the refusal in
 * *chat* — the agent says "approve it in the panel" and there is no panel on
 * screen. That is the third time in this feature that a refusal pointed at a
 * place the user could not reach, and the pattern is always the same: the
 * message names a destination, and the destination lives somewhere narrower
 * than the message travels.
 *
 * So this is not a panel component. It follows whichever agent the user is
 * talking to and appears above everything, because a question the agent is
 * blocked on outranks whatever else is on screen — the alternative is an
 * agent that looks hung.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw } from 'lucide-react';

import { api } from '@/lib/api';
import { useChatStore } from '@/stores';
import type { ApprovalLifetime, BrowserLoginRequest, BrowserPendingRequest } from '@/types/browser';
import BrowserApprovalPrompt from '@/components/artifacts/renderers/BrowserApprovalPrompt';
import BrowserLoginNotice from './BrowserLoginNotice';
import { Button } from '@/components/nm/button';
import { PANELS, useRegistryEntries } from '@/platform/registries';

/** How often to ask. Approvals are raised by an agent turn, not by this tab. */
const POLL_MS = 2000;

function isLoginRequest(notice: BrowserPendingRequest): notice is BrowserLoginRequest {
  return 'kind' in notice && notice.kind === 'login';
}

export function BrowserApprovalNotice() {
  const agentId = useChatStore((s) => s.activeAgentId);
  const browserAvailable = useRegistryEntries(PANELS).some((entry) => entry.id === 'browser');
  // A different agent owns different requests, errors and answered IDs.
  return agentId && browserAvailable ? <AgentApprovalNotice key={agentId} agentId={agentId} /> : null;
}

function AgentApprovalNotice({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState<BrowserPendingRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [retry, setRetry] = useState(0);
  const answered = useRef(new Set<string>());

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      setChecking(true);
      try {
        const res = await api.getBrowserApprovals(agentId);
        if (!active) return;
        setPending(res.pending.filter((a) => a.agent_id === agentId && !answered.current.has(a.id)));
        setError(null);
      } catch (e) {
        if (!active) return;
        setPending([]);
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (active) {
          setChecking(false);
          // Schedule only after settlement, so slow polls cannot overlap.
          timer = setTimeout(() => { void load(); }, POLL_MS);
        }
      }
    };
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [agentId, retry]);

  const decide = async (
    approvalId: string,
    decision: 'allow' | 'deny',
    lifetime: ApprovalLifetime,
  ) => {
    const result = await api.resolveBrowserApproval(approvalId, decision, lifetime);
    if (result?.ok !== true) {
      throw new Error(t('browser.approval.decisionFailed', 'Could not save the decision. Please try again.'));
    }
    // Drop it immediately rather than waiting for the next poll: leaving an
    // answered question on screen invites a second, contradictory answer.
    answered.current.add(approvalId);
    setPending((list) => list.filter((a) => a.id !== approvalId));
  };

  if (pending.length === 0 && error === null) return null;

  return (
    <div data-testid="browser-approval-notice">
      {error !== null && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--nm-hairline)] p-3">
          <p role="alert" className="min-w-0 break-words text-xs text-[var(--color-error)]">
            {t('browser.approval.loadFailed', 'Could not check browser approvals.')} {error}
          </p>
          <Button variant="secondary" size="sm" loading={checking}
            leading={<RefreshCw className="h-3.5 w-3.5" />}
            onClick={() => setRetry((n) => n + 1)}>
            {t('browser.approval.retry', 'Try again')}
          </Button>
        </div>
      )}
      {pending.map((a) => isLoginRequest(a) ? <BrowserLoginNotice key={a.id} request={a} /> : (
        <BrowserApprovalPrompt
          key={a.id}
          approval={a}
          onDecide={(decision, lifetime) => decide(a.id, decision, lifetime)}
        />
      ))}
    </div>
  );
}

export default BrowserApprovalNotice;
