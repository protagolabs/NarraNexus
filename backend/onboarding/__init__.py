"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-08-19
@description: Onboarding guide-agent subpackage (backend-side per 铁律 #21 —
its only consumers are the login routes).

DELIBERATELY EMPTY of imports: `naming.py` is also consumed by
backend/integrations/arena/arena_onboarding.py, a pure-HTTP module with no
DB/settings coupling — an eager re-export of `provisioning` here would drag
AsyncDatabaseClient (and the whole provisioning stack) into every
`import backend.onboarding.naming`. Consumers import their module directly:
the login hooks use `backend.onboarding.provisioning` (whose import also
registers the "onboarding" bootstrap profile as a side effect — see the note
at its import block), Arena uses `backend.onboarding.naming`.
"""
