"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: NarraNexus kernel + public contracts (plugin platform, batch 0).

Layering, enforced by import-linter (see pyproject ``[tool.importlinter]``):

    narranexus.contracts   imports nothing from kernel / platform / legacy
    narranexus.kernel      imports contracts only
    xyz_agent_context.*    may import both (legacy package; migrates in
                           later batches of the plugin-platform roadmap)

The package is deliberately tiny in batch 0: the value is the boundary,
not the volume.
"""

__version__ = "0.1.0"
