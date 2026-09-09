# Changelog

## Unreleased

- `submit_feedback` gains an optional `dedup_key`: with it set, the tool files a
  report once per agent per key (TTL- and size-bounded, in-process) and silently
  drops repeats, so the Product Feedback Duty's machine-generated trigger
  (a rejected platform-injected credential) cannot flood the intake during a
  platform-wide outage. A report that never reached the intake releases its slot.

## 1.0.0

- Packaged as an independent workspace member (plugin platform batch 6b).
