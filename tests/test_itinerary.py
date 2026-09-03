"""Unit tests for travel_itinerary_builder.itinerary.

The .ics assertions parse the rendered bytes back with icalendar itself
(rather than checking the raw text) so a test failure means the calendar is
genuinely wrong, not just differently formatted.
"""

from datetime import date, datetime

from icalendar import Calendar

from travel_itinerary_builder.extractor import ExtractedLeg
from travel_itinerary_builder.itinerary import (
    render_trip_ics,
    render_trip_markdown,
    slug_for_trip,
)


def make_leg(
    leg_type: str,
    start: datetime,
    end: datetime,
    start_has_time: bool = True,
    end_has_time: bool = True,
    **overrides,
) -> ExtractedLeg:
    base = dict(
        leg_type=leg_type,
        provider="Test Provider",
        confirmation_number="CONF123",
        traveler_name="Test Traveler",
        start_datetime=start,
        start_has_time=start_has_time,
        start_location="Start Place",
        end_datetime=end,
        end_has_time=end_has_time,
        end_location="End Place",
        cost=100.0,
        currency="GBP",
        notes="",
    )
    base.update(overrides)
    return ExtractedLeg(**base)


def _sample_trip():
    """A small, unambiguous 3-leg trip: flight in, hotel, car - each on a
    distinct date, so chronological order is never ambiguous."""
    flight = make_leg(
        "flight",
        datetime(2026, 6, 12, 10, 15),
        datetime(2026, 6, 12, 13, 5),
        provider="British Airways",
        start_location="LHR",
        end_location="JFK",
        confirmation_number="XJ7K2P",
    )
    hotel = make_leg(
        "hotel",
        datetime(2026, 6, 13, 0, 0),
        datetime(2026, 6, 15, 0, 0),
        start_has_time=False,
        end_has_time=False,
        provider="New York Marriott Marquis",
        start_location="1535 Broadway, New York",
        end_location="1535 Broadway, New York",
        confirmation_number="MR998271",
    )
    car = make_leg(
        "car",
        datetime(2026, 6, 15, 11, 0),
        datetime(2026, 6, 16, 11, 0),
        provider="Hertz",
        start_location="JFK",
        end_location="JFK",
        confirmation_number="HZ55",
    )
    return [hotel, flight, car]  # deliberately given out of order


def test_markdown_contains_every_leg_in_chronological_order():
    trip = _sample_trip()

    markdown = render_trip_markdown(trip)

    flight_idx = markdown.index("British Airways")
    hotel_idx = markdown.index("New York Marriott Marquis")
    car_idx = markdown.index("Hertz")
    assert flight_idx < hotel_idx < car_idx
    assert "XJ7K2P" in markdown
    assert "MR998271" in markdown
    assert "HZ55" in markdown


def test_same_day_flight_renders_before_hotel_checkin():
    """The single most common trip pattern: fly in, check into a hotel later
    the same day. The hotel's check-in has no stated time (start_has_time=
    False) and lands on the SAME calendar date as the flight's departure -
    exactly the case _sort_start's 15:00 default exists for. Without it, the
    date-only leg sorts at a literal midnight and renders first, showing
    "check into hotel" ahead of "board flight"."""
    flight = make_leg(
        "flight", datetime(2026, 6, 12, 8, 30), datetime(2026, 6, 12, 11, 20),
        provider="British Airways", start_location="LHR", end_location="JFK",
    )
    hotel = make_leg(
        "hotel", datetime(2026, 6, 12, 0, 0), datetime(2026, 6, 15, 0, 0),
        start_has_time=False, end_has_time=False,
        provider="New York Marriott Marquis",
    )
    trip = [hotel, flight]  # given out of order, same as the bug report

    markdown = render_trip_markdown(trip)
    flight_idx = markdown.index("British Airways")
    hotel_idx = markdown.index("New York Marriott Marquis")
    assert flight_idx < hotel_idx, (
        "flight must render before the same-day hotel check-in"
    )

    # The .ics event order matters too - most calendar apps display same-day
    # events in the order they were added.
    cal = Calendar.from_ical(render_trip_ics(trip))
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    summaries = [str(e.get("summary")) for e in events]
    assert summaries.index("Flight (British Airways): LHR → JFK") < summaries.index(
        "Hotel: New York Marriott Marquis"
    )

    # The displayed/encoded values must NOT be contaminated by the sort-only
    # 15:00 placeholder - the hotel is still a genuine date-only leg.
    hotel_event = next(e for e in events if str(e.get("summary")).startswith("Hotel"))
    assert hotel_event.get("dtstart").dt == date(2026, 6, 12), (
        "the .ics start must stay a plain DATE, not a fabricated 15:00 DATE-TIME"
    )


def test_markdown_is_never_empty_for_a_real_trip():
    markdown = render_trip_markdown(_sample_trip())
    assert markdown.strip()
    assert markdown.startswith("# Trip:")


def test_ics_output_is_structurally_valid():
    trip = _sample_trip()

    ics_bytes = render_trip_ics(trip)
    parsed = Calendar.from_ical(ics_bytes)

    events = [c for c in parsed.walk() if c.name == "VEVENT"]
    assert len(events) == 3

    flight_event = next(e for e in events if "British Airways" in str(e["summary"]))
    assert flight_event["dtstart"].dt == datetime(2026, 6, 12, 10, 15)
    assert flight_event["dtend"].dt == datetime(2026, 6, 12, 13, 5)

    hotel_event = next(e for e in events if "Marriott" in str(e["summary"]))
    # All-day event (no explicit check-in/out time): dtstart/dtend must be
    # plain dates, not datetimes, so calendar apps render it as all-day.
    assert hotel_event["dtstart"].dt == date(2026, 6, 13)
    assert hotel_event["dtend"].dt == date(2026, 6, 15)

    car_event = next(e for e in events if "Hertz" in str(e["summary"]))
    assert car_event["dtstart"].dt == datetime(2026, 6, 15, 11, 0)


def test_ics_all_day_same_day_event_does_not_render_zero_length():
    # start and end date-only fall on the same day - dtend must be pushed
    # forward so this isn't rendered as a zero-length all-day event.
    leg = make_leg(
        "car",
        datetime(2026, 6, 20, 0, 0),
        datetime(2026, 6, 20, 0, 0),
        start_has_time=False,
        end_has_time=False,
    )

    parsed = Calendar.from_ical(render_trip_ics([leg]))
    event = next(c for c in parsed.walk() if c.name == "VEVENT")

    assert event["dtstart"].dt == date(2026, 6, 20)
    assert event["dtend"].dt == date(2026, 6, 21)


def test_slug_for_trip_is_filesystem_safe():
    slug = slug_for_trip(_sample_trip())

    assert slug
    assert " " not in slug
    assert slug == slug.lower()
    assert all(c.isalnum() or c == "-" for c in slug)


def test_render_empty_trip_does_not_crash():
    assert "No legs" in render_trip_markdown([])
