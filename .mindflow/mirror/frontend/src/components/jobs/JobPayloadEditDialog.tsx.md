---
code_file: frontend/src/components/jobs/JobPayloadEditDialog.tsx
last_verified: 2026-09-09
stub: false
---

# JobPayloadEditDialog.tsx — edit a job's title/description/payload

## Why it exists

The backend PUT /api/jobs/{job_id} route (mirrors the job_update MCP
tool) accepted title/description/payload from day one, but no frontend
surface ever wrote to it — the panel could view a job's payload
(JobExpandedDetail) but never edit it (GitHub #86). This is the
content-edit sibling of JobScheduleEditDialog, which already covers the
job's execution time through a separate route
(/api/dashboard/jobs/{id}/schedule). The two stay separate components
because they're separate backend routes with separate field sets — a
single dialog editing both would have to reconcile two different save
paths for no benefit.

## Upstream / downstream

- Opened by `JobExpandedDetail`'s "Edit Content" button
  (`canEditPayload`/`onEditPayload` props), gated by the same
  `canEdit(status)` predicate JobsPanel already uses for the schedule
  editor — editing either the timing or the content of a
  running/terminal job is equally meaningless.
- `JobsPanel` owns the open/saving state and calls
  `api.updateJob(jobId, agentId, fields)` on submit, then
  `refreshJobs()` on success (same shape as `handleSaveSchedule`).

## Design decisions

- Only the fields the user actually changed are included in the
  `onSave` payload — matches the backend's `JobUpdateFields`
  None-means-unchanged contract, and mirrors
  JobScheduleEditDialog's "only send the diff" rule exactly.
- Client-side validation is a single check (title required, non-blank
  after trim) — the payload/description have no format constraint
  worth enforcing here; the backend is the source of truth for anything
  deeper.
- `saving`/error display follow the same prop contract as
  JobScheduleEditDialog: this component holds no network state of its
  own, the parent (`JobsPanel`) owns success/failure handling.

## Gotcha

- Passing an all-unchanged form (title/description/payload identical to
  the loaded job) calls `onClose()` instead of `onSave({})` — an empty
  PUT body would still hit the network for nothing, and the backend
  would report `updated_fields: []` either way.
