"""Unit tests for travel_itinerary_builder.grouping.

Pure logic, no network, no filesystem - hand-built ExtractedLeg objects only.
"""

from datetime import datetime

from travel_itinerary_builder.extractor import ExtractedLeg
from travel_itinerary_builder.grouping import dedupe_legs, group_legs_into_trips


def make_leg(start: datetime, end: datetime, **overrides) -> ExtractedLeg:
    base = dict(
        leg_type="flight",
        provider="Test Air",
        confirmation_number="ABC123",
        traveler_name="Test Traveler",
        start_datetime=start,
        start_has_time=True,
        start_location="AAA",
        end_datetime=end,
        end_has_time=True,
        end_location="BBB",
        cost=None,
        currency="",
        notes="",
    )
    base.update(overrides)
    return ExtractedLeg(**base)


def test_legs_one_day_apart_group_into_one_trip():
    leg1 = make_leg(datetime(2026, 6, 1, 10, 0), datetime(2026, 6, 1, 14, 0))
    leg2 = make_leg(datetime(2026, 6, 2, 9, 0), datetime(2026, 6, 2, 11, 0))

    trips = group_legs_into_trips([leg1, leg2])

    assert len(trips) == 1
    assert trips[0] == [leg1, leg2]


def test_legs_ten_days_apart_do_not_group_with_default_gap():
    leg1 = make_leg(datetime(2026, 6, 1, 10, 0), datetime(2026, 6, 1, 14, 0))
    leg2 = make_leg(datetime(2026, 6, 11, 9, 0), datetime(2026, 6, 11, 11, 0))

    trips = group_legs_into_trips([leg1, leg2])

    assert len(trips) == 2
    assert trips[0] == [leg1]
    assert trips[1] == [leg2]


def test_gap_days_threshold_is_configurable():
    leg1 = make_leg(datetime(2026, 6, 1, 10, 0), datetime(2026, 6, 1, 14, 0))
    leg2 = make_leg(datetime(2026, 6, 4, 9, 0), datetime(2026, 6, 4, 11, 0))

    # ~3 day gap - groups under a generous 5-day threshold...
    assert len(group_legs_into_trips([leg1, leg2], gap_days=5)) == 1

    # ...but not under a tight 1-day threshold.
    assert len(group_legs_into_trips([leg1, leg2], gap_days=1)) == 2


def test_empty_list_returns_no_trips():
    assert group_legs_into_trips([]) == []


def test_three_legs_form_two_trips():
    leg1 = make_leg(datetime(2026, 6, 1, 10, 0), datetime(2026, 6, 1, 14, 0))
    leg2 = make_leg(datetime(2026, 6, 2, 9, 0), datetime(2026, 6, 2, 11, 0))
    leg3 = make_leg(datetime(2026, 6, 20, 9, 0), datetime(2026, 6, 20, 11, 0))

    trips = group_legs_into_trips([leg1, leg2, leg3])

    assert len(trips) == 2
    assert trips[0] == [leg1, leg2]
    assert trips[1] == [leg3]


def test_input_order_does_not_matter_output_is_sorted():
    leg1 = make_leg(datetime(2026, 6, 1, 10, 0), datetime(2026, 6, 1, 14, 0))
    leg2 = make_leg(datetime(2026, 6, 2, 9, 0), datetime(2026, 6, 2, 11, 0))

    trips = group_legs_into_trips([leg2, leg1])

    assert trips == [[leg1, leg2]]


# ---------------------------------------------------------------------------
# dedupe_legs - the same real booking described by two confirmation emails
# ---------------------------------------------------------------------------


def test_dedupe_collapses_the_same_booking_described_twice():
    """Mirrors a real case found via live testing: a hotel booking forwarded
    as both a "confirmation" and a later "hotel details" email produced two
    near-identical legs - same type/provider/dates/cost, but two different
    confirmation numbers (a short display code vs. the underlying itinerary
    number) and different notes."""
    confirmation_email_leg = make_leg(
        datetime(2026, 5, 9, 0, 0),
        datetime(2026, 5, 14, 0, 0),
        leg_type="hotel",
        provider="Park MGM Las Vegas (booked via Expedia)",
        confirmation_number="2TODYXP3XY",
        cost=525.21,
        currency="GBP",
        notes="1 room, Park MGM King, 2 adults, 5 nights.",
    )
    hotel_details_email_leg = make_leg(
        datetime(2026, 5, 9, 0, 0),
        datetime(2026, 5, 14, 0, 0),
        leg_type="hotel",
        provider="Park MGM Las Vegas (booked via Expedia)",
        confirmation_number="72069055891360",
        cost=525.21,
        currency="GBP",
        notes=(
            "1 room, Park MGM King; 2 adults; 5 nights (avg 62.45/night); "
            "accommodation 354.05 + taxes & fees 41.79."
        ),
    )

    deduped = dedupe_legs([confirmation_email_leg, hotel_details_email_leg])

    assert len(deduped) == 1
    kept = deduped[0]
    # The longer, more informative notes win as the primary record...
    assert kept.notes.startswith("1 room, Park MGM King; 2 adults; 5 nights")
    # ...but the other email's confirmation number isn't silently lost.
    assert "2TODYXP3XY" in kept.notes


def test_dedupe_leaves_genuinely_different_legs_alone():
    flight = make_leg(datetime(2026, 5, 9, 8, 0), datetime(2026, 5, 9, 11, 0))
    hotel = make_leg(
        datetime(2026, 5, 9, 0, 0),
        datetime(2026, 5, 14, 0, 0),
        leg_type="hotel",
        provider="Park MGM Las Vegas",
    )

    deduped = dedupe_legs([flight, hotel])

    assert deduped == [flight, hotel]


def test_dedupe_is_a_noop_when_confirmation_numbers_already_match():
    leg = make_leg(datetime(2026, 5, 9, 0, 0), datetime(2026, 5, 14, 0, 0))
    identical_copy = make_leg(datetime(2026, 5, 9, 0, 0), datetime(2026, 5, 14, 0, 0))

    deduped = dedupe_legs([leg, identical_copy])

    assert len(deduped) == 1
    assert deduped[0].notes == ""
