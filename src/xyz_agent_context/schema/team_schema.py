"""
@file_name: team_schema.py
@author: NetMind.AI
@date: 2026-05-08
@description: Team & TeamMember Pydantic models

Subproject 1: Team Membership
- Team: a named group of agents
- TeamMember: many-to-many between teams and agents

Source values for Team.source:
- "user": created by the user via UI
- "bundle:<bundle_id>": auto-created from a bundle import (subproject 2)
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class Team(BaseModel):
    id: Optional[int] = None
    team_id: str
    owner_user_id: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    source: str = "user"
    intro_md: Optional[str] = None
    # Agent that answers a team-chat message with no @mention (None = earliest member).
    lead_agent_id: Optional[str] = None
    # Leader patrol. Read-only here on purpose: `patrol.py` and the patrol
    # toggle own these columns, and routing them through the team CRUD's
    # _entity_to_row would let an unrelated team edit reset the cursor.
    # None on patrol_enabled = undecided, which reads as ON for a team that
    # HAS a lead (setting a lead IS the act of saying "this one is responsible").
    patrol_enabled: Optional[bool] = None
    last_patrol_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TeamMember(BaseModel):
    id: Optional[int] = None
    team_id: str
    agent_id: str
    joined_at: Optional[datetime] = None


# ===== API request / response =====


class CreateTeamRequest(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = None


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    intro_md: Optional[str] = None
    lead_agent_id: Optional[str] = None


class AddMemberRequest(BaseModel):
    agent_id: str


class TeamWithMembers(BaseModel):
    team: Team
    member_agent_ids: List[str] = Field(default_factory=list)


class TeamListResponse(BaseModel):
    teams: List[TeamWithMembers]


class TeamOperationResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    team: Optional[Team] = None
