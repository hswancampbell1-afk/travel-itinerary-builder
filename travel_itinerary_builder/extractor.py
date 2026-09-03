"""Turn one plain-text travel booking confirmation into a structured "leg".

Modeled closely on calendar-scheduler's scheduler/nlp_parser.py: forced
tool-use against a `strict: true` schema, one call per input, and a
model-reported `unparseable` flag rather than trying to force unrelated text
(a newsletter, a receipt for something else) into a fake leg. `extract_leg`
never raises - it always returns either an `ExtractedLeg` or a
`LegExtractionError`, so callers (the CLI) can treat "this wasn't a booking
confirmation" as a normal, expected outcome.

This module depends only on the narrow `AnthropicMessagesClient` protocol
from anthropic_client.py, not on the `anthropic` package itself, so it can be
unit-tested with a mock client - no network, no API key.

Note on `cost`/`currency`: strict-mode tool schemas don't support a nullable
type (no `"type": ["number", "null"]`), so "no cost stated" is carried by
`currency` being an empty string rather than `cost` being JSON null - the
schema asks for that explicitly, and `_validate_and_build` below turns an
empty `currency` into `cost=None` on the Python side regardless of whatever
placeholder number the model filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal, Optional, Union

from .anthropic_client import DEFAULT_MODEL, AnthropicMessagesClient

TOOL_NAME = "extract_travel_leg"

LEG_TYPE_VALUES = ("flight", "hotel", "car", "train", "other")
LegType = Literal["flight", "hotel", "car", "train", "other"]

EXTRACTION_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record the structured fields extracted from one travel booking "
        "confirmation (flight, hotel, car rental, or train), or flag the "
        "text as not a booking confirmation at all."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "unparseable": {
                "type": "boolean",
                "description": (
                    "true if this text genuinely isn't a travel booking "
                    "confirmation - a newsletter, an unrelated receipt, "
                    "spam, etc. When true, the other fields should still "
                    "be present but may hold placeholder values - only "
                    "'reason' matters."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "If unparseable is true, a short human-readable reason "
                    "why. Empty string otherwise."
                ),
            },
            "leg_type": {
                "type": "string",
                "enum": list(LEG_TYPE_VALUES),
                "description": "The kind of booking this confirmation is for.",
            },
            "provider": {
                "type": "string",
                "description": (
                    "The airline, hotel chain, rental company, or rail "
                    "operator name."
                ),
            },
            "confirmation_number": {
                "type": "string",
                "description": "The booking/confirmation reference, if given.",
            },
            "traveler_name": {
                "type": "string",
                "description": (
                    "The traveler's name as stated in the confirmation. "
                    "Empty string if not stated."
                ),
            },
            "start_datetime": {
                "type": "string",
                "description": (
                    "ISO-8601 date or datetime (YYYY-MM-DD or "
                    "YYYY-MM-DDTHH:MM) - departure time for a flight, "
                    "check-in date for a hotel, pickup time for a car, "
                    "departure time for a train. A bare date with no time "
                    "is expected and fine for a hotel check-in."
                ),
            },
            "start_location": {
                "type": "string",
                "description": (
                    "Departure airport/station/city, or the hotel/rental "
                    "office address or city."
                ),
            },
            "end_datetime": {
                "type": "string",
                "description": (
                    "ISO-8601 date or datetime - arrival time for a "
                    "flight/train, check-out date for a hotel, drop-off "
                    "time for a car."
                ),
            },
            "end_location": {
                "type": "string",
                "description": "Arrival airport/station/city, or drop-off location.",
            },
            "cost": {
                "type": "number",
                "description": (
                    "The booking cost as a plain number. Only meaningful "
                    "when currency is non-empty - use 0 as a placeholder "
                    "when no cost is stated."
                ),
            },
            "currency": {
                "type": "string",
                "description": (
                    "3-letter currency code (e.g. GBP, USD, EUR) if a cost "
                    "is stated. Empty string if no cost is stated anywhere "
                    "in the text - this is what marks cost as not given, "
                    "since a null cost isn't representable here."
                ),
            },
            "notes": {
                "type": "string",
                "description": (
                    "Anything else worth keeping - seat number, room type, "
                    "car class, fare class, baggage allowance, etc. Empty "
                    "string if there's nothing extra worth noting."
                ),
            },
        },
        "required": [
            "unparseable",
            "reason",
            "leg_type",
            "provider",
            "confirmation_number",
            "traveler_name",
            "start_datetime",
            "start_location",
            "end_datetime",
            "end_location",
            "cost",
            "currency",
            "notes",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass(frozen=True)
class ExtractedLeg:
    """One successfully extracted travel leg."""

    leg_type: LegType
    provider: str
    confirmation_number: str
    traveler_name: str
    start_datetime: datetime
    start_has_time: bool
    start_location: str
    end_datetime: datetime
    end_has_time: bool
    end_location: str
    cost: Optional[float]
    currency: str
    notes: str
    raw_text: str = field(repr=False, default="")


@dataclass(frozen=True)
class LegExtractionError:
    """Returned instead of an ExtractedLeg when extraction fails.

    Covers both the model judging the text unparseable and the model's (or a
    mock's) output failing schema validation - callers handle both the same
    way: report the reason, move on to the next input.
    """

    reason: str
    raw_text: str = field(repr=False, default="")


ExtractionResult = Union[ExtractedLeg, LegExtractionError]


def _build_prompt(text: str) -> str:
    return (
        "You are extracting structured fields from ONE travel booking "
        "confirmation (flight, hotel, car rental, or train), given as "
        "plain text below - this may be an email body, a forwarded "
        "confirmation, or similar. Extract exactly one leg.\n\n"
        "If the text is not a booking confirmation at all (e.g. a "
        "newsletter, a receipt for something unrelated, or spam), set "
        "unparseable to true and give a short reason - do not force-fit "
        "unrelated text into a fake leg.\n\n"
        "Only use dates/times/numbers actually stated in the text - never "
        "invent a value that isn't there. Use ISO-8601 (YYYY-MM-DD or "
        "YYYY-MM-DDTHH:MM) for start_datetime and end_datetime; a bare "
        "date with no time is expected and fine for something like a "
        "hotel check-in/check-out date.\n\n"
        f"Confirmation text:\n{text}"
    )


def _extract_tool_input(message: Any) -> Optional[dict]:
    """Pull the extraction tool's input dict out of a Messages API response.

    Returns None if no matching tool_use block is present - a model (or a
    badly-written mock) that ignores the tool entirely is a data problem,
    not a bug in this function.
    """
    content = getattr(message, "content", None)
    if not content:
        return None
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != TOOL_NAME:
            continue
        tool_input = getattr(block, "input", None)
        if isinstance(tool_input, dict):
            return tool_input
    return None


def _parse_flexible_datetime(raw: str) -> Optional[tuple]:
    """Parse an ISO-8601 date or datetime string.

    Returns (datetime, had_explicit_time) or None if `raw` can't be parsed
    at all. A bare date ("2026-06-01") is valid and common (hotel
    check-in/out) and comes back with had_explicit_time=False, midnight
    filled in as a placeholder - itinerary.py uses that flag to render an
    all-day .ics event instead of a midnight one.
    """
    raw = raw.strip()
    if not raw:
        return None

    try:
        parsed_date = date.fromisoformat(raw)
    except ValueError:
        pass
    else:
        return datetime.combine(parsed_date, time.min), False

    for candidate in (raw, raw.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate), True
        except ValueError:
            continue
    return None


def _validate_and_build(raw: dict, source_text: str) -> ExtractionResult:
    """Validate a tool-input dict and turn it into an ExtractionResult.

    Defensive by design: this runs on model output (or, in tests, hand-built
    mock output), so every field is checked before use. Nothing here should
    ever raise - any structural problem becomes a LegExtractionError.
    """
    if not isinstance(raw, dict):
        return LegExtractionError(
            reason="Model response was not a structured object.",
            raw_text=source_text,
        )

    if raw.get("unparseable"):
        reason = raw.get("reason") or (
            "This text could not be understood as a booking confirmation."
        )
        return LegExtractionError(reason=str(reason), raw_text=source_text)

    required = (
        "leg_type",
        "provider",
        "confirmation_number",
        "traveler_name",
        "start_datetime",
        "start_location",
        "end_datetime",
        "end_location",
        "cost",
        "currency",
        "notes",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        return LegExtractionError(
            reason=f"Model response was missing field(s): {', '.join(missing)}.",
            raw_text=source_text,
        )

    leg_type = raw["leg_type"]
    if leg_type not in LEG_TYPE_VALUES:
        return LegExtractionError(
            reason=f"leg_type '{leg_type}' was not one of {LEG_TYPE_VALUES}.",
            raw_text=source_text,
        )

    start_parsed = _parse_flexible_datetime(str(raw["start_datetime"]))
    if start_parsed is None:
        return LegExtractionError(
            reason=(
                f"start_datetime '{raw['start_datetime']}' was not a valid "
                f"ISO-8601 date/datetime."
            ),
            raw_text=source_text,
        )
    end_parsed = _parse_flexible_datetime(str(raw["end_datetime"]))
    if end_parsed is None:
        return LegExtractionError(
            reason=(
                f"end_datetime '{raw['end_datetime']}' was not a valid "
                f"ISO-8601 date/datetime."
            ),
            raw_text=source_text,
        )

    start_dt, start_has_time = start_parsed
    end_dt, end_has_time = end_parsed
    if end_dt < start_dt:
        return LegExtractionError(
            reason="end_datetime is before start_datetime.",
            raw_text=source_text,
        )

    currency = str(raw["currency"]).strip().upper()
    cost: Optional[float] = None
    if currency:
        cost_raw = raw["cost"]
        if not isinstance(cost_raw, (int, float)) or isinstance(cost_raw, bool):
            return LegExtractionError(
                reason="cost was not a number despite a currency being given.",
                raw_text=source_text,
            )
        cost = float(cost_raw)

    return ExtractedLeg(
        leg_type=leg_type,  # type: ignore[arg-type]
        provider=str(raw["provider"]).strip(),
        confirmation_number=str(raw["confirmation_number"]).strip(),
        traveler_name=str(raw["traveler_name"]).strip(),
        start_datetime=start_dt,
        start_has_time=start_has_time,
        start_location=str(raw["start_location"]).strip(),
        end_datetime=end_dt,
        end_has_time=end_has_time,
        end_location=str(raw["end_location"]).strip(),
        cost=cost,
        currency=currency,
        notes=str(raw["notes"]).strip(),
        raw_text=source_text,
    )


def extract_leg(
    text: str,
    client: AnthropicMessagesClient,
    model: str = DEFAULT_MODEL,
) -> ExtractionResult:
    """Extract one travel leg from one confirmation text.

    Args:
        text: the raw confirmation text (an email body, etc).
        client: any object exposing `.messages.create(**kwargs)` with the
            same shape as `anthropic.Anthropic().messages` - in production a
            real Anthropic client (see anthropic_client.build_client), in
            tests a mock.
        model: the Claude model id to request.

    Returns:
        An `ExtractedLeg` on success, or a `LegExtractionError` on any
        failure - empty input, a model that flags the text as unparseable,
        an API error, or a response that doesn't match the expected schema.
        This function never raises for those cases.
    """
    source_text = (text or "").strip()
    if not source_text:
        return LegExtractionError(
            reason="Empty input - nothing to extract.", raw_text=text
        )

    try:
        message = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=[{"role": "user", "content": _build_prompt(source_text)}],
        )
    except Exception as exc:  # noqa: BLE001 - any client failure (network,
        # auth, rate limit, SDK-internal) must degrade to a graceful
        # extraction error, never a crash - same reasoning as
        # calendar-scheduler's parse_scheduling_request.
        return LegExtractionError(
            reason=f"Anthropic API call failed: {exc}", raw_text=source_text
        )

    tool_input = _extract_tool_input(message)
    if tool_input is None:
        return LegExtractionError(
            reason="Model did not return the expected structured data.",
            raw_text=source_text,
        )

    return _validate_and_build(tool_input, source_text)
