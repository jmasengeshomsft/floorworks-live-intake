"""Validated model output, independent of transport and persistence."""
from __future__ import annotations

import json
from typing import Annotated, Literal
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(max_length=4000)]
Name = Annotated[str, StringConstraints(max_length=200)]
Priority = Literal["low", "normal", "medium", "high", "urgent", "critical"]


class IntakeFields(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    __pydantic_extra__: dict[str, JsonValue]
    customer_name: Name | None = None
    phone: Annotated[str, StringConstraints(max_length=30)] | None = None
    equipment_model: Name | None = None
    equipment_serial_or_asset: Annotated[str, StringConstraints(max_length=100)] | None = None
    location: Name | None = None
    preferred_time: Name | None = None
    complaint: Text | None = None
    summary: Text | None = None
    priority: Priority | None = None

    @field_validator("priority", mode="before")
    @classmethod
    def priority_case(cls, value):
        return value.lower() if isinstance(value, str) else value


class Part(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    number: Name
    name: Name
    unit_cost: float = Field(ge=0, le=1e12)


class Enrichment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    likely_cause: Text | None = None
    bulletin: Text | None = None
    parts: list[Part] = Field(default_factory=list, max_length=100)
    suggested_priority: Priority | None = None
    priority_reason: Text | None = None
    diagnostic_steps: list[Text] = Field(default_factory=list, max_length=100)
    sources: list[Text] = Field(default_factory=list, max_length=100)

    @field_validator("suggested_priority", mode="before")
    @classmethod
    def priority_case(cls, value):
        return value.lower() if isinstance(value, str) else value


class Match(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: Literal["bdm_equipment", "bdm_serviceticket", "bdm_customer", "bdm_contact"]
    id: UUID
    label: Name = ""
    reason: Text = ""


class ExtractionResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    fields: IntakeFields
    enrichment: Enrichment | None = None
    matches: list[Match] = Field(default_factory=list, max_length=100)
    confidence: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)
    missing: list[Name] = Field(default_factory=list, max_length=100)
    next_question: Text | None = None
    created: None = None
    action: Literal["none"] = "none"
    error: None = None


def check_schema(schema: dict) -> None:
    Draft202012Validator.check_schema(schema)
    # Schema validation must never fetch an external document using service credentials.
    def check(value):
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in value and not value[key].startswith("#"):
                    raise ValueError("EXTRACTION_SCHEMA permits only local references")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(schema)


def validate_result(data: dict, schema: dict | None = None) -> dict:
    if len(json.dumps(data, allow_nan=False).encode("utf-8")) > 262144:
        raise ValueError("extraction output exceeds 256 KiB")
    result = ExtractionResult.model_validate(data).model_dump(mode="json", exclude_unset=True)
    if schema:
        error = next(Draft202012Validator(schema).iter_errors(result["fields"]), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path) or "fields"
            raise ValueError(f"extraction schema validation failed at {path}: {error.validator}")
    return result
