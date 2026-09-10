/**
 * useCircuitBannerAutoClear — while the "agent paused" circuit-breaker banner
 * is up, re-check the agent's real breaker status on an interval and close
 * the banner once the backend no longer reports it paused (the half-open
 * probe succeeded, or the owner fixed the key from another tab/device).
 *
 * Extracted from App.tsx so the polling contract is testable with fake
 * timers instead of living inside the root component's effect soup:
 *   - checks immediately, then every CIRCUIT_BREAKER_POLL_INTERVAL_MS;
 *   - keyed on (agentId, reason, isLoggedIn) — NOT the banner object, which
 *     is a fresh reference on every rejected turn and would otherwise reset
 *     the interval on each retry, so the first poll never fired for exactly
 *     the users retrying most;
 *   - only while logged in: polling an authenticated endpoint after logout
 *     just produces 401s, and the banner itself is cleared on logout;
 *   - a failed fetch leaves the banner as-is (next tick retries) — a network
 *     blip must never dismiss a banner for an agent that is still paused.
 */
import { useEffect } from 'react';

import { api } from '@/lib/api';
import {
  CIRCUIT_BREAKER_POLL_INTERVAL_MS,
  shouldClearCircuitBanner,
  type AgentCircuitOpenDetail,
} from '@/services/wsCircuitOpen';

export function useCircuitBannerAutoClear(
  circuitOpen: AgentCircuitOpenDetail | null,
  setCircuitOpen: (next: AgentCircuitOpenDetail | null) => void,
  isLoggedIn: boolean,
): void {
  const agentId = circuitOpen?.agentId ?? null;
  const reason = circuitOpen?.reason ?? '';

  useEffect(() => {
    if (!agentId) return;
    if (!isLoggedIn) {
      // Nothing to poll and nothing worth showing to a logged-out user.
      setCircuitOpen(null);
      return;
    }
    if (!reason.startsWith('paused')) return;
    let cancelled = false;
    const pollStatus = async () => {
      try {
        const status = await api.getAgentCircuitBreaker(agentId);
        if (!cancelled && shouldClearCircuitBanner(status.cb_status)) {
          setCircuitOpen(null);
        }
      } catch {
        // Best-effort re-check — leave the banner as-is; the next tick retries.
      }
    };
    void pollStatus();
    const id = window.setInterval(() => void pollStatus(), CIRCUIT_BREAKER_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
    // setCircuitOpen is a React state setter (stable identity).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, reason, isLoggedIn]);
}
