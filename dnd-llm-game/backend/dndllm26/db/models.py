from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    setting: str = "A dangerous frontier realm full of ruins, factions, and secrets."
    tone: str = "heroic fantasy"
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Character(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    name: str
    ancestry: str
    character_class: str
    backstory: str
    inventory_json: str = "[]"
    is_human: bool = False


class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    ancestry: str = "Human"
    character_class: str = "Fighter"
    backstory: str = "An adventurer looking for a reason to risk everything."
    inventory_json: str = "[]"
    is_human: bool = False
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Turn(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    speaker: str
    content: str
    created_at: datetime = Field(default_factory=now_utc)


class LoreDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    status: str = "queued"
    chunks: int = 0
    created_at: datetime = Field(default_factory=now_utc)
    error: str | None = None


class CampaignLore(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    lore_document_id: int = Field(index=True, foreign_key="loredocument.id")


class GameEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    event_type: str = Field(index=True)
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=now_utc)


class PendingRoll(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    action_text: str
    formula: str = "1d20"
    ability: str = "Ability"
    skill: str | None = None
    dc: int = 10
    reason: str
    narration: str = ""
    status: str = Field(default="pending", index=True)
    created_at: datetime = Field(default_factory=now_utc)
    resolved_at: datetime | None = None


class DiceRoll(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id")
    pending_roll_id: int | None = Field(default=None, index=True, foreign_key="pendingroll.id")
    formula: str
    rolls_json: str
    modifier: int = 0
    total: int
    dc: int | None = None
    outcome: str = "rolled"
    reason: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class WorldState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(index=True, foreign_key="campaign.id", unique=True)
    current_location: str = "Unknown"
    active_objective: str = "Find an adventure."
    scene_summary: str = ""
    choices_json: str = "[]"
    updated_at: datetime = Field(default_factory=now_utc)
