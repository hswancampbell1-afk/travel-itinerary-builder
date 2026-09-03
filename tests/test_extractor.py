"""Unit tests for travel_itinerary_builder.extractor.

Every test here runs with a fake Anthropic client - no network access, no
ANTHROPIC_API_KEY. The fake client mimics just enough of the real
`anthropic.Anthropic().messages.create(...)` surface (a response object
whose `.content` holds tool_use blocks with `.type`, `.name`, `.input`) for
`extract_leg` to consume.
"""

from datetime import datetime
from types import SimpleNamespace

from travel_itinerary_builder.extractor import (
    TOOL_NAME,
    ExtractedLeg,
    LegExtractionError,
    extract_leg,
)


def make_tool_use_block(tool_input: dict, name: str = TOOL_NAME) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=tool_input)


def make_message(*blocks) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


class FakeMessages:
    def __init__(self, response=None, raise_error: Exception = None):
        self._response = response
        self._raise_error = raise_error
        self.last_call_kwargs = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        if self._raise_error is not None:
            raise self._raise_error
        return self._response


class FakeAnthropicClient:
    def __init__(self, response=None, raise_error: Exception = None):
        self.messages = FakeMessages(response=response, raise_error=raise_error)


def full_tool_input(**overrides) -> dict:
    """A complete, valid tool-input dict, so each test only specifies the
    fields it cares about - mirrors calendar-scheduler's own test style."""
    base = {
        "unparseable": False,
        "reason": "",
        "leg_type": "flight",
        "provider": "British Airways",
        "confirmation_number": "XJ7K2P",
        "traveler_name": "Harrison Campbell",
        "start_datetime": "2026-06-12T10:15:00",
        "start_location": "London Heathrow (LHR)",
        "end_datetime": "2026-06-12T13:05:00",
        "end_location": "New York JFK (JFK)",
        "cost": 412.5,
        "currency": "GBP",
        "notes": "Seat 14C, Economy (Basic)",
    }
    base.update(overrides)
    return base


FLIGHT_TEXT = (
    "Flight BA0178 confirmation. Booking reference: XJ7K2P. LHR -> JFK, "
    "12 June 2026, 10:15 - 13:05. Passenger: Harrison Campbell."
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_flight_confirmation_extracts_correctly():
    tool_input = full_tool_input()
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, ExtractedLeg)
    assert result.leg_type == "flight"
    assert result.provider == "British Airways"
    assert result.confirmation_number == "XJ7K2P"
    assert result.start_datetime == datetime(2026, 6, 12, 10, 15)
    assert result.start_has_time is True
    assert result.end_datetime == datetime(2026, 6, 12, 13, 5)
    assert result.end_has_time is True
    assert result.cost == 412.5
    assert result.currency == "GBP"


def test_hotel_confirmation_extracts_correctly_with_date_only_checkin():
    tool_input = full_tool_input(
        leg_type="hotel",
        provider="New York Marriott Marquis",
        confirmation_number="MR998271",
        start_datetime="2026-06-12",
        start_location="1535 Broadway, New York, NY 10036",
        end_datetime="2026-06-15",
        end_location="1535 Broadway, New York, NY 10036",
        cost=867.0,
        currency="USD",
        notes="King Deluxe, non-smoking",
    )
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg("hotel confirmation text", client)

    assert isinstance(result, ExtractedLeg)
    assert result.leg_type == "hotel"
    assert result.start_datetime == datetime(2026, 6, 12, 0, 0)
    assert result.start_has_time is False
    assert result.end_datetime == datetime(2026, 6, 15, 0, 0)
    assert result.end_has_time is False
    assert result.cost == 867.0
    assert result.currency == "USD"


# ---------------------------------------------------------------------------
# Non-booking text must be flagged, never force-fit
# ---------------------------------------------------------------------------


def test_non_booking_text_is_flagged_unparseable_not_force_fit():
    tool_input = full_tool_input(
        unparseable=True,
        reason="This is a marketing newsletter, not a booking confirmation.",
    )
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(
        "50% off all flights this weekend only! Click here to unsubscribe.",
        client,
    )

    assert isinstance(result, LegExtractionError)
    assert "newsletter" in result.reason.lower()


def test_empty_input_short_circuits_without_calling_api():
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(full_tool_input())))

    result = extract_leg("   ", client)

    assert isinstance(result, LegExtractionError)
    assert client.messages.last_call_kwargs is None


# ---------------------------------------------------------------------------
# cost / currency "not stated" convention
# ---------------------------------------------------------------------------


def test_no_cost_stated_leaves_cost_none_regardless_of_placeholder_number():
    tool_input = full_tool_input(cost=0, currency="")
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, ExtractedLeg)
    assert result.cost is None
    assert result.currency == ""


# ---------------------------------------------------------------------------
# Malformed / unexpected model output - must fail gracefully, never crash
# ---------------------------------------------------------------------------


def test_model_response_missing_tool_use_block_fails_gracefully():
    text_block = SimpleNamespace(type="text", text="Sure, I can help with that!")
    client = FakeAnthropicClient(response=make_message(text_block))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)
    assert "structured data" in result.reason.lower()


def test_invalid_leg_type_is_rejected():
    tool_input = full_tool_input(leg_type="bus")
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)


def test_invalid_start_datetime_is_rejected():
    tool_input = full_tool_input(start_datetime="not-a-date")
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)


def test_end_before_start_is_rejected():
    tool_input = full_tool_input(
        start_datetime="2026-06-12T10:00:00", end_datetime="2026-06-12T09:00:00"
    )
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)
    assert "before" in result.reason.lower()


def test_missing_required_field_fails_gracefully():
    tool_input = full_tool_input()
    del tool_input["provider"]
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)
    assert "provider" in result.reason


def test_api_error_fails_gracefully_not_a_crash():
    client = FakeAnthropicClient(raise_error=ConnectionError("network unreachable"))

    result = extract_leg(FLIGHT_TEXT, client)

    assert isinstance(result, LegExtractionError)
    assert "network unreachable" in result.reason


# ---------------------------------------------------------------------------
# Request shape sanity check
# ---------------------------------------------------------------------------


def test_request_forces_the_extraction_tool_and_passes_model_arg():
    tool_input = full_tool_input()
    client = FakeAnthropicClient(response=make_message(make_tool_use_block(tool_input)))

    extract_leg(FLIGHT_TEXT, client, model="claude-opus-5")

    kwargs = client.messages.last_call_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert kwargs["tools"][0]["name"] == TOOL_NAME
    assert kwargs["tools"][0]["strict"] is True
    assert kwargs["tools"][0]["input_schema"]["additionalProperties"] is False
