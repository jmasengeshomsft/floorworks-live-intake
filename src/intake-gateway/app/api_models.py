"""Validated rep-facing mutation contracts."""
from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strict=True, max_length=200)]
Phone = Annotated[str, StringConstraints(strict=True, max_length=30)]
Notes = Annotated[str, StringConstraints(strict=True, max_length=4000)]


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customerId: UUID | None = None
    equipmentId: UUID | None = None
    contactId: UUID | None = None
    customername: Name | None = None
    contactphone: Phone | None = None
    equipmenttext: Name | None = None
    assetnumber: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    location: Name | None = None
    preferredtime: Name | None = None
    summary: Notes | None = None
    complaint: Notes | None = None
    priority: Literal["low", "normal", "medium", "high", "urgent", "critical"] | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, value):
        return value.lower() if isinstance(value, str) else value

    @field_validator("priority")
    @classmethod
    def priority_cannot_be_cleared(cls, value):
        if value is None:
            raise ValueError("priority cannot be cleared")
        return value


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
    phone: Phone | None = None


class SubmitDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submittedBy: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)] = "rep"


class FunctionResult(RootModel[dict[str, JsonValue]]):
    @model_validator(mode="after")
    def limit_size(self):
        if len(json.dumps(self.root).encode("utf-8")) > 262144:
            raise ValueError("function result exceeds 256 KiB")
        return self


class StatusResponse(BaseModel):
    status: Literal["ok", "accepted"]


class TicketResponse(BaseModel):
    ticket: str = Field(min_length=1)
