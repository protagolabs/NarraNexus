"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-08-19
@description: Onboarding guide-agent subpackage. Importing it registers the
"onboarding" bootstrap profile (side effect, like the arena profile) and
exposes the provisioning entry point the login hooks call.
"""

from xyz_agent_context.bootstrap.onboarding import profile as _profile  # noqa: F401 — registry side effect
