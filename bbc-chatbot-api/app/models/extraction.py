"""Typed contract for the save_travel_details tool-call boundary.

The Costa incident proved untyped tool output reaches the lead: whatever
shape the model returns is merged as fact. This model kills the phantom-
and partial-json classes AT THE SOURCE: unknown keys are rejected
(extra="forbid"), the 24/7 idiom can never become a date, dates must be
plausible, passengers bounded, return after departure.

These are the codebase's FIRST ConfigDict models — the discipline starts
here.
"""

import re
from datetime import date, timedelta
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

_IDIOM_24_7 = re.compile(r"^\s*24\s*/\s*7\s*$")

_VALID_TRIP_TYPES = ("one_way", "round_trip", "multi_city", "open_jaw")
_VALID_CABINS = ("business", "first", "economy", "mixed")


class ExtractedEntities(BaseModel):
    """Mirror of the TRAVEL_TOOL arguments (+ the fields extraction may grow)."""

    model_config = ConfigDict(extra="forbid")

    origin: Optional[str] = None
    destination: Optional[str] = None
    departure_date: Optional[str] = None
    return_date: Optional[str] = None
    trip_type: Optional[str] = None
    passengers: Optional[int] = None
    cabin_class: Optional[str] = None

    @field_validator("departure_date", "return_date")
    @classmethod
    def _plausible_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if _IDIOM_24_7.match(v):
            raise ValueError("'24/7' is an availability idiom, not a date")
        d = date.fromisoformat(v)  # ValueError on any non-ISO shape
        today = date.today()
        if not (today - timedelta(days=1) <= d <= today + timedelta(days=730)):
            raise ValueError(f"date {v} outside [today-1d, today+2y]")
        return v

    @field_validator("origin", "destination")
    @classmethod
    def _iata_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        code = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError(f"'{v}' is not a 3-letter IATA code")
        return code

    @field_validator("trip_type")
    @classmethod
    def _known_trip_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_TRIP_TYPES:
            raise ValueError(f"trip_type must be one of {_VALID_TRIP_TYPES}")
        return v

    @field_validator("passengers")
    @classmethod
    def _pax_bounds(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 9:
            raise ValueError("passengers must be 1-9")
        return v

    @field_validator("cabin_class")
    @classmethod
    def _known_cabin(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_CABINS:
            raise ValueError(f"cabin_class must be one of {_VALID_CABINS}")
        return v

    @model_validator(mode="after")
    def _return_after_departure(self) -> "ExtractedEntities":
        if (
            self.departure_date
            and self.return_date
            and self.return_date < self.departure_date
        ):
            raise ValueError("return_date is before departure_date")
        return self


def validate_tool_entities(raw: dict) -> tuple[Optional[dict], Optional[str]]:
    """(clean_entities, None) on success — (None, error_text) on failure.

    The error text is meant to be fed back to the model for ONE correction
    re-prompt.
    """
    try:
        model = ExtractedEntities.model_validate(raw or {})
    except ValidationError as e:
        return None, str(e)
    return {k: v for k, v in model.model_dump().items() if v is not None}, None


def salvage_tool_entities(raw: dict) -> dict:
    """Last resort after a failed re-prompt: keep only the individually valid
    known fields, drop everything else. The reply must never crash over a bad
    tool argument."""
    out: dict = {}
    for key, val in (raw or {}).items():
        if key not in ExtractedEntities.model_fields:
            continue
        try:
            d = ExtractedEntities.model_validate({key: val}).model_dump()
        except ValidationError:
            continue
        if d.get(key) is not None:
            out[key] = d[key]
    if (
        out.get("departure_date")
        and out.get("return_date")
        and out["return_date"] < out["departure_date"]
    ):
        out.pop("return_date")
    return out
