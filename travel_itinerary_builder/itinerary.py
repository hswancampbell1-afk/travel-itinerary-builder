"""Render one trip (an ordered list of ExtractedLeg) to markdown and .ics.

Nothing in this module talks to the network or the Anthropic API - it only
turns already-extracted, already-grouped legs into the two output formats.
The .ics output is built with the `icalendar` package rather than hand-rolled
iCalendar text, so line-folding, escaping, and VALUE=DATE handling for
all-day events are the library's problem, not ours.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Sequence

from icalendar import Calendar, Event

from .extractor import ExtractedLeg

_LEG_TYPE_LABELS = {
    "flight": "Flight",
    "hotel": "Hotel",
    "car": "Car",
    "train": "Train",
    "other": "Travel",
}

# Real-world default check-in time, used ONLY to break ties when ordering
# legs for display - never written into the rendered output itself
# (_format_leg_dt and the .ics all-day handling both still show/encode a
# date-only leg as date-only). Without this, a date-only hotel check-in
# sorts at midnight and can render BEFORE a same-day timed flight, showing
# "check into hotel" ahead of "board flight" on the single most common
# pattern a trip has: fly in, check in later the same day. 15:00 is an
# ordinary global hotel check-in convention, not this traveler's actual
# time - the point is only to place the leg on the right side of same-day
# timed legs, not to claim precision the extraction never had. Only START
# time affects ordering (legs are ordered as one list by when they begin;
# nothing here ever sorts by end time), so there's no equivalent
# check-out constant.
_DEFAULT_CHECKIN_HOUR = 15


def _sort_start(leg: ExtractedLeg) -> datetime:
    if leg.start_has_time:
        return leg.start_datetime
    hour = _DEFAULT_CHECKIN_HOUR if leg.leg_type == "hotel" else 12
    return leg.start_datetime.replace(hour=hour)


def _leg_summary(leg: ExtractedLeg) -> str:
    label = _LEG_TYPE_LABELS.get(leg.leg_type, "Travel")
    provider = leg.provider or "Unknown provider"
    if leg.leg_type in ("flight", "train") and leg.start_location and leg.end_location:
        return f"{label} ({provider}): {leg.start_location} → {leg.end_location}"
    return f"{label}: {provider}"


def _format_leg_dt(dt: datetime, has_time: bool) -> str:
    if has_time:
        return dt.strftime("%a %d %b %Y, %H:%M")
    return dt.strftime("%a %d %b %Y")


def _leg_description(leg: ExtractedLeg) -> str:
    parts = []
    if leg.confirmation_number:
        parts.append(f"Confirmation #: {leg.confirmation_number}")
    if leg.traveler_name:
        parts.append(f"Traveler: {leg.traveler_name}")
    if leg.cost is not None:
        parts.append(f"Cost: {leg.cost:.2f} {leg.currency}")
    if leg.notes:
        parts.append(f"Notes: {leg.notes}")
    return "\n".join(parts)


def render_trip_markdown(trip: Sequence[ExtractedLeg]) -> str:
    """Render one trip's legs to a clean, human-readable markdown document.

    Legs are re-sorted by _sort_start here (rather than trusting caller
    order), so this function produces a correct chronological document even
    if it's ever called directly, outside grouping.group_legs_into_trips.
    _sort_start gives a date-only hotel check-in a nominal 15:00 for
    ordering purposes only, so it correctly renders after a same-day timed
    flight rather than before it at a literal midnight - see _sort_start's
    own comment. The displayed text (_format_leg_dt) is unaffected and still
    shows a date-only leg as a date, never a fabricated time.
    """
    if not trip:
        return "# Trip\n\n_No legs in this trip._\n"

    ordered = sorted(trip, key=_sort_start)
    start_date = ordered[0].start_datetime.date()
    end_date = max(leg.end_datetime for leg in ordered).date()
    date_range = (
        start_date.isoformat()
        if start_date == end_date
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    lines = [f"# Trip: {date_range}", ""]
    for leg in ordered:
        lines.append(f"## {_leg_summary(leg)}")
        lines.append("")
        lines.append(f"- **Type:** {leg.leg_type}")
        lines.append(f"- **Provider:** {leg.provider or '(not stated)'}")
        lines.append(
            f"- **Start:** {_format_leg_dt(leg.start_datetime, leg.start_has_time)} "
            f"– {leg.start_location or '(not stated)'}"
        )
        lines.append(
            f"- **End:** {_format_leg_dt(leg.end_datetime, leg.end_has_time)} "
            f"– {leg.end_location or '(not stated)'}"
        )
        if leg.confirmation_number:
            lines.append(f"- **Confirmation #:** {leg.confirmation_number}")
        if leg.traveler_name:
            lines.append(f"- **Traveler:** {leg.traveler_name}")
        if leg.cost is not None:
            lines.append(f"- **Cost:** {leg.cost:.2f} {leg.currency}")
        if leg.notes:
            lines.append(f"- **Notes:** {leg.notes}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_trip_ics(trip: Sequence[ExtractedLeg]) -> bytes:
    """Render one trip's legs to a standard .ics calendar (one VEVENT each).

    A leg with no explicit time (start_has_time/end_has_time False - the
    normal case for a hotel check-in/check-out date) becomes an all-day
    event (DATE value, not DATE-TIME) instead of a midnight-to-midnight one.
    Per the iCalendar spec, an all-day DTEND is exclusive, so a same-day
    all-day booking (or one whose end date isn't after its start date) gets
    its end pushed forward one day - otherwise calendar apps would render it
    as zero-length.
    """
    ordered = sorted(trip, key=_sort_start)

    cal = Calendar()
    cal.add("prodid", "-//travel-itinerary-builder//vatools//")
    cal.add("version", "2.0")

    for leg in ordered:
        event = Event()
        event.add("summary", _leg_summary(leg))

        if leg.start_has_time:
            event.add("dtstart", leg.start_datetime)
        else:
            event.add("dtstart", leg.start_datetime.date())

        if leg.end_has_time:
            event.add("dtend", leg.end_datetime)
        else:
            end_date = leg.end_datetime.date()
            if end_date <= leg.start_datetime.date():
                end_date = leg.start_datetime.date() + timedelta(days=1)
            event.add("dtend", end_date)

        event.add("dtstamp", datetime.now())
        event["uid"] = f"{uuid.uuid4()}@travel-itinerary-builder"

        location = leg.start_location or leg.end_location
        if location:
            event.add("location", location)

        description = _leg_description(leg)
        if description:
            event.add("description", description)

        cal.add_component(event)

    return cal.to_ical()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def slug_for_trip(trip: Sequence[ExtractedLeg]) -> str:
    """A filesystem-safe basename for this trip's output files.

    Built from the trip's date range plus a representative destination/
    provider, mirroring the sanitisation approach already used elsewhere in
    this suite (e.g. va-control-panel's review/service.py _slugify) rather
    than inventing new unsafe-character handling.
    """
    if not trip:
        return "trip"
    ordered = sorted(trip, key=_sort_start)
    start_date = ordered[0].start_datetime.date().isoformat()
    end_date = max(leg.end_datetime for leg in ordered).date().isoformat()
    representative = (
        ordered[0].end_location or ordered[0].provider or ordered[0].leg_type
    )
    slug = _slugify(f"{start_date}-to-{end_date}-{representative}")
    return slug[:80] or "trip"
