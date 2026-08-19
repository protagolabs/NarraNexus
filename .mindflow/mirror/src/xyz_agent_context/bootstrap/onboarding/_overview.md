# bootstrap/onboarding/ — the auto-provisioned guide agent

Domain subpackage (铁律 #23) for the "new user's first agent": when a user
logs in for the first time (or an existing user with zero agents logs in), the
login hooks in `backend/routes/auth.py` fire-and-forget
`provisioning.ensure_guide_agent`, which creates a randomly-named,
randomly-personified companion agent that greets the user immediately (static
bilingual bootstrap greeting — zero LLM cost, zero latency), teaches them
NarraNexus (via the `narranexus-guide` marketplace skill), and checks in once
a day through a native `ongoing` job (max_iterations hard ceiling + LLM-judged
end_condition + agent-side 3-strikes goodbye).

Files:
- `personas.py` — content pools (personas, topic openers) + greeting /
  awareness / Bootstrap.md renderers. Pure data, no IO.
- `profile.py` — the "onboarding" BootstrapProfile (registered on import).
- `provisioning.py` — the orchestration seam the login hooks call: idempotency
  (user-level metadata marker), provision_new_agent, skill install, check-in
  job, marker write.

Everything here lives in `src` (not `backend/`) because every dependency —
provision seam, repositories, job service, skill marketplace — is src-side;
the backend keeps only thin fire-and-forget schedulers.
