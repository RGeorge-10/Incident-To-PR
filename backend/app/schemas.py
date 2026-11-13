from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class IncidentCreate(BaseModel):
    service: ShortText
    summary: ShortText
    source: Literal["manual", "alertmanager"] = "manual"
    labels: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    decision: Literal["approve_pr", "reject"]
    reviewer: ShortText
    comment: str = Field(default="", max_length=2000)


class EscalateRequest(BaseModel):
    reviewer: ShortText
    comment: str = Field(default="", max_length=2000)


class AlertmanagerPayload(BaseModel):
    receiver: str | None = None
    status: str = "firing"
    alerts: list[dict[str, Any]] = Field(default_factory=list)


class RepositoryConfigUpdate(BaseModel):
    code_repository_url: str = Field(min_length=1, max_length=500)
    gitops_repository_url: str = Field(default="", max_length=500)


class MonitorConfigUpdate(BaseModel):
    enabled: bool
    auto_analyze: bool = False


class SessionModelUpdate(BaseModel):
    model: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:/-]+$")
    api_key: str = Field(min_length=8, max_length=400)
