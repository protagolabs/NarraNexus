---
code_file: src/xyz_agent_context/bootstrap/onboarding/provisioning.py
last_verified: 2026-08-19
stub: false
---

# provisioning.py — login-time guide-agent provisioning seam

## Why it exists

New users land in an empty product and have to invent their first prompt.
`ensure_guide_agent(db, user_id)` gives every brand-new user (and existing
users who own zero agents) a ready-made companion that speaks first — without
the user doing anything. Called fire-and-forget from all three login paths
(`netmind_login`, local `login`, local `create_user` in
`backend/routes/auth.py`); it must never block or fail a login.

## Upstream / Downstream

**Callers:** `backend/routes/auth.py::_schedule_guide_agent_provisioning`
(create_task + done_callback, incident-lesson-#2 pattern).
**Uses:** `bootstrap/naming.generate_name`, `onboarding/personas.py` picks +
awareness renderer, the shared `provision_new_agent` seam, `apply_bootstrap`
with the "onboarding" profile, `SkillMarketplaceService.install`
(narranexus-guide), `JobInstanceService.create_job_with_instance`, and
`UserRepository` for the marker.

## Design decisions

- **User-level idempotency marker** (`users.metadata.onboarding_progress.
  guide_agent_provisioned`, write-once, merge-and-write like the checklist
  endpoint). Agent-level tags alone would resurrect the guide after the user
  deletes it — deletion must stick. The agent still gets
  `agent_metadata.provisioned_source="onboarding"` for ops/statistics.
- **has-agents users get the marker, not an agent**: someone already using
  the product doesn't need a stranger pinging them; writing the marker makes
  later logins skip before the agents query.
- **`provision_new_agent(bootstrap_profile="none")` then a second
  `apply_bootstrap`** with the extras-laden ctx (persona_key / topic_index /
  is_local) — the shared seam can't carry ctx.extra; same split Arena uses.
- **The check-in job is ACTIVE, not paused** (unlike Arena's consent-gated
  routines): the proactive daily touch IS the feature. It is bounded three
  ways — `max_iterations=14` (JobTrigger-enforced, no model cooperation
  needed), the LLM-judged `end_condition`, and the payload's 3-ignored-
  check-ins goodbye + self-pause. First fire is created-at + 24h
  (compute_next_run: interval jobs fire at base + interval), so it never
  races the greeting.
- **Kill-switch env `NARRANEXUS_ONBOARDING_GUIDE_AGENT` defaults ON**: dev
  gets it on merge, prod at the next release, no deploy-repo env change;
  "0"/"false"/"no" disables.
- **Best-effort everywhere after the agent row exists**: tag / bootstrap /
  skill / job failures fold into warnings; the marker is still written (a
  partially-provisioned guide is still a guide; retrying on next login would
  double-greet).

## Gotchas

- Concurrent first logins can race past the marker check; the second call
  then sees the first's agent in the has-agents branch. A sub-second double
  provision is theoretically possible and accepted (same posture as Arena).
- The greeting text itself lives in personas.py and is stored at provision
  time — editing copy here changes only future users.
