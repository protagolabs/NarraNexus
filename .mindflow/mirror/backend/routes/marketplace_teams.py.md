---
code_file: backend/routes/marketplace_teams.py
last_verified: 2026-07-21
stub: false
---

# routes/marketplace_teams.py — /api/marketplace/teams/*

The teams half of the /api/marketplace namespace (skills/* is the sibling,
reserved for exactly this at main.py mount time). GET list/detail/download
are public reads (desktop clients fetch anonymously — auth.py's marketplace
public-read prefix list now covers teams/* too). install-preflight resolves
identity + runs the LOCAL importer via the service; confirm reuses the
existing POST /api/bundle/import/confirm (zero new install code). publish/
delete are staff-gated on cloud, open in local mode (loopback + OS-user
boundary), mirroring the skill publish policy. Route order: /download and
/install-preflight declared before /{template_id} (FastAPI matches in order).
Install is fork semantics — no per-user installation audit table; forked
agents/teams ARE the record (team.source = 'bundle:<id>').
