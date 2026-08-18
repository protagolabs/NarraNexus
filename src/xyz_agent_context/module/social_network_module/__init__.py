"""
Social Network Module

Provides social network recording and search capabilities
"""

from .social_network_module import (
    SocialNetworkModule,
    social_instance_not_found_msg,
    format_contact_result,
    format_stats_result,
    format_create_agent_success,
    CREATE_AGENT_NO_OWNER_MSG,
    CREATE_AGENT_EMPTY_NAME_MSG,
)

__all__ = [
    "SocialNetworkModule",
    "social_instance_not_found_msg",
    "format_contact_result",
    "format_stats_result",
    "format_create_agent_success",
    "CREATE_AGENT_NO_OWNER_MSG",
    "CREATE_AGENT_EMPTY_NAME_MSG",
]
