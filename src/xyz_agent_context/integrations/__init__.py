"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: External-platform integration clients, grouped by platform.

`services/` is for background workers (pollers, sync loops, alert
watchers). Clients that talk to external platforms live here instead:

- `netmind/` — NetMind auth / billing / key provisioning / power-account
  and the user-identity migration service.
- `arena/` — Agent Arena auto-provisioning.
- `feedback_client.py` — central feedback intake client.

No re-exports: consumers import the client modules explicitly.
"""
