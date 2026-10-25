from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


RiskLevel = Literal["read", "low", "high"]


class MissionStatus(StrEnum):
    DRAFTING = "DRAFTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    AWAITING_STEP_APPROVAL = "AWAITING_STEP_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TAKEN_OVER = "TAKEN_OVER"


class PlanStep(BaseModel):
    id: str
    title: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    requires_confirmation: bool = False
    expected_result: str
    status: str = "PENDING"
    result: dict[str, Any] | None = None


class MissionPlan(BaseModel):
    mission_id: str
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStep]

    @model_validator(mode="after")
    def ensure_steps(self) -> "MissionPlan":
        if not self.steps:
            raise ValueError("mission plan must contain at least one step")
        return self


class MissionState(BaseModel):
    mission_id: str
    prompt: str
    status: MissionStatus
    plan: MissionPlan | None = None
    revision: int = 1
    next_step_index: int = 0
    approved: bool = False
    approved_step_id: str | None = None
    error: str | None = None
    created_at: float
    updated_at: float
