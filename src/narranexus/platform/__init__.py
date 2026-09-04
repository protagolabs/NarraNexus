"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: The platform layer — what every plugin needs and no plugin can be: today the turn pipeline (``platform.turn``).

Depends on the kernel and the contracts; may import the legacy
``xyz_agent_context`` domain packages while they migrate here. Never imports
a plugin (import-linter enforces it once ``plugins/`` exists).
"""
