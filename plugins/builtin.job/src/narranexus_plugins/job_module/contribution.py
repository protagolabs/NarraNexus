"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.job`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.platform.channel.contributions import module_contribution, trigger_contribution
from narranexus.contracts.trigger import TriggerSpec

PLUGIN_ID = "builtin.job"
MODULES = (module_contribution("narranexus_plugins.job_module.job_module:JobModule", PLUGIN_ID, channel=False),)

# The job clock runs as a worker of the workers supervisor (bare name "jobs": run.sh / compose address it with --only/--exclude).
TRIGGERS = (trigger_contribution(TriggerSpec("jobs", "narranexus_plugins.job_module.job_trigger:JobTrigger", host="workers", kwargs={"poll_interval": 60, "max_workers": 5})),)

__all__ = ["MODULES", "PLUGIN_ID", "TRIGGERS"]
