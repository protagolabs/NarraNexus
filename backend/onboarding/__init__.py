"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-08-19
@description: Onboarding guide-agent subpackage (backend-side per 铁律 #21 —
its only consumers are the login routes). Importing it REGISTERS the
"onboarding" bootstrap profile (side effect, like the arena profile) and
exposes the provisioning entry points the login hooks call.
"""

# Load-bearing side-effect import: profile.py's import-time register_profile()
# is what makes get_profile("onboarding") resolve. Removing this line would
# NOT raise anywhere — get_profile falls back to the "default" profile on an
# unknown name — so every guide agent would silently render the generic
# blank-slate first-run instead of its persona greeting.
from backend.onboarding import profile as _profile  # noqa: F401
from backend.onboarding.provisioning import (  # noqa: F401
    ensure_guide_agent,
    is_backfill_enabled,
    is_guide_agent_enabled,
)
