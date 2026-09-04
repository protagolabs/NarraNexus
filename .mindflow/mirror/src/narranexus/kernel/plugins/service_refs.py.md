---
code_file: src/narranexus/kernel/plugins/service_refs.py
last_verified: 2026-09-04
stub: false
---

# kernel/plugins/service_refs.py — service refs builtins expose

## Intent

Typed `ServiceRef`s for the three services the platform used to reach by importing a builtin: `skills.workspaces` (builtin.skills: `(agent_id, user_id) -> SkillWorkspace`), `jobs.instances` (builtin.job: `(db) -> JobInstanceService`), `jobs.run_once` (builtin.job: `async (agent_id, job_id) -> JobRunOutcome`). Exposed on `Registries.services` by `module/contributions.register_all` (SERVICE_SPECS); consumed through `utils/plugin_services`. `remove_owner` releases them, so a disabled builtin's service is simply absent.
