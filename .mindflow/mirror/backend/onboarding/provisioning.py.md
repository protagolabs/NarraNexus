---
code_file: backend/onboarding/provisioning.py
last_verified: 2026-08-19
stub: false
---

# provisioning.py — login-time guide-agent provisioning seam

## Why it exists

New users land in an empty product and have to invent their first prompt.
`ensure_guide_agent(db, user_id, is_new_user=...)` gives every brand-new user
(and existing zero-agent users, behind the backfill brake) a ready-made
companion that speaks first — without the user doing anything. Called
fire-and-forget from all three login paths (`netmind_login`, local `login`,
local `create_user` in `backend/routes/auth.py`); it must never block or fail
a login.

## Upstream / Downstream

**Callers:** `backend/routes/auth.py::_schedule_guide_agent_provisioning`
(create_task + done_callback, incident-lesson-#2 pattern; checks the
kill-switch before creating any task).
**Uses:** `naming.generate_name`, `personas.py` picks + awareness renderer,
the shared `provision_new_agent` seam (single call, carrying
`bootstrap_profile="onboarding"` + `bootstrap_ctx_extra`),
`SkillMarketplaceService.install` (narranexus-guide),
`JobInstanceService.create_job_with_instance`, `UserRepository` for the
marker. Zero raw SQL.

## Design decisions

- **User-level idempotency marker at a TOP-LEVEL metadata key**
  (`users.metadata.guide_agent_provisioned`, write-once, merge-and-write).
  Top-level on purpose: `POST /api/auth/onboarding` wholesale-replaces the
  `onboarding_progress` sub-dict (three fixed checklist booleans), so a
  marker nested there would be wiped the first time the user created an
  agent in the UI — re-provisioning forever, and resurrecting the guide
  after the user deleted it. Regression test:
  `tests/backend/test_onboarding.py::test_post_never_clobbers_the_guide_agent_marker`.
- **Claim FIRST**: the marker is written BEFORE provisioning (after the
  has-agents check), plus a per-user in-process `asyncio.Lock` serializes
  concurrent calls (two tabs from one ?token= link, client retries, local
  create-user→login). The cloud backend is single-process, so this closes
  the realistic double-provision race; a multi-process deployment would
  reopen a small cross-process window (claim-first shrinks it to one DB
  round-trip). Lock entries are popped unconditionally after release — that
  can discard a lock queued coroutines still hold, letting a later arrival
  skip their queue; accepted because claim-first is the actual backstop and
  stale entries must not leak across event loops. The concurrency test's
  fakes carry real awaits so deleting the lock goes red. The flip side of
  claim-first — a crash mid-provision leaves the user guide-less with no
  retry — is deliberate: a retry that half-succeeded once would
  double-greet.
- **Two env levers with opposite defaults**: `NARRANEXUS_ONBOARDING_GUIDE_AGENT`
  (master kill-switch, default ON; any of 0/false/no/off/disabled/empty
  turns it off — incident-keyboard spellings included) and
  `NARRANEXUS_ONBOARDING_GUIDE_BACKFILL` (existing zero-agent users,
  **default OFF, explicit truthy opt-in**). The backfill population is the
  unbounded cost face (N historical accounts × a daily agent-loop each, on
  free-tier wallets, including known sock-puppet cohorts) — ops flips it to
  1 after measuring the zero-agent population; new signups need no flag.
  The frontend learns the master switch via the login response's
  `guide_agent_provisioning` field, so pulling it also silences the
  coachmark. `is_*_enabled()` read os.environ per call, but a deployed
  container needs an env change + restart to flip.
- **The daily check-in is a SCHEDULED job, deliberately NOT "ongoing"**: an
  ONGOING job's iteration counter and end_condition Helper-LLM analysis also
  run on EVERY chat event (`hook_after_event_execution`), which would (a)
  burn a max_iterations budget on ordinary conversation — a chatty first
  week would silently COMPLETE the "daily companionship" — and (b) add one
  LLM call to every chat turn of every new user (the exact cost face that
  blew the monthly limit on 8/13). The hard stop is PLATFORM-enforced via
  `trigger_config.end_at` (provision-time + `CHECKIN_END_AFTER_DAYS`; the
  scheduling-horizon primitive added to TriggerConfig/JobTrigger in this
  same change — no model cooperation needed). Softer exits remain: user
  pause/cancel in the Jobs panel (greeting says how) and the payload's
  3-ignored-check-ins goodbye + self-pause; the payload's end-date sentence
  is the polite goodbye script for the horizon, stamped from the SAME
  instant so script and brake can never disagree — and worded
  "{end_date} or later", NOT "after {end_date}": the horizon day gets the
  LAST fire (the next one would land past end_at and the platform completes
  the job right after), so an "after"-worded script would never run and the
  guide would vanish mid-smalltalk with no goodbye.
- **has-agents users get the marker, not an agent**: someone already using
  the product doesn't need a stranger pinging them; the marker makes later
  logins skip before the agents query (`find_one`, not a full find).
- **First fire ≈ +24h** (compute_next_run: interval jobs fire at base +
  interval; rows start PENDING and the poller fires pending+active), so the
  check-in never races the greeting.
- **Best-effort after the row exists**: tag / skill / job failures fold into
  warnings; failed bootstrap OR awareness seeding is logged at ERROR (a mute
  guide / a persona-less generic assistant — and nothing retries either).
- **Job title has no emoji on purpose**: "Daily check-in" is simultaneously
  the payload's retrieval string, the awareness quote, and the
  find_active_by_title dedup key — an emoji is the part most likely to break
  retrieval-side matching and silently kill the agent's self-pause.
- **The profile registration side-effect import lives at THIS module's
  import block** (`import backend.onboarding.profile`), not the package
  `__init__` — one unambiguous registration point on the production path,
  pinned by test_importing_provisioning_registers_the_profile.

## Gotchas

- `users.timezone` is validated through `_safe_timezone` (ZoneInfo probe →
  "UTC") before it reaches TriggerConfig — a stored non-IANA value would
  otherwise reject the whole job.
- The greeting/persona content lives in personas.py and is stored at
  provision time — editing copy changes only future users.
- Integration coverage (real DB, no collaborator mocks):
  `tests/backend/onboarding/test_onboarding_provisioning_integration.py`
  pins the persisted rows; the unit file pins parameter pipes only.
