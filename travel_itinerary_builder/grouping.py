"""Group successfully-extracted travel legs into trips by date proximity.

Nothing in this module talks to the network, the filesystem, or the
Anthropic API - it only takes a list of `ExtractedLeg` objects (already
extracted by extractor.py) and clusters them. Callers are responsible for
separating unparseable inputs out first: this module only ever sees legs
that extracted successfully, and never silently drops or guesses at one -
see cli.py for where unparseable inputs are collected and reported instead.

Callers should also run `dedupe_legs` before `group_legs_into_trips` - see
its own docstring for why (the same booking described by more than one
confirmation email must not become two legs in the output).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import List, Sequence

from .extractor import ExtractedLeg

# Default gap (in days) between one leg's end and the next leg's start that
# is still considered "the same trip". Exposed as a CLI flag (--gap-days)
# rather than hardcoded, since what counts as "still one trip" is a genuine
# judgment call that varies by traveler.
DEFAULT_GAP_DAYS = 4


def dedupe_legs(legs: Sequence[ExtractedLeg]) -> List[ExtractedLeg]:
    """Collapse legs that describe the same real-world booking twice.

    Found via real testing: a booking is often confirmed by more than one
    email (an initial "confirmation" and a later "hotel details" or
    "e-ticket" follow-up), and each is extracted independently since
    extractor.py only ever sees one input file at a time. Two legs are
    treated as the same booking when leg_type, provider, start_datetime,
    end_datetime, cost and currency all match exactly.
    confirmation_number is deliberately EXCLUDED from that check - the same
    real booking is routinely quoted under two different reference numbers
    across its own confirmation emails (e.g. a short display code in one,
    the underlying itinerary number in the other), so requiring it to match
    would defeat the point of this function.

    The kept leg is whichever of the pair has the longer `notes` (treated as
    a proxy for "more informative"); if the discarded leg had a different,
    non-empty confirmation_number, it's folded into the kept leg's notes so
    that reference number isn't silently lost.

    Order-preserving and does not otherwise sort - call before
    group_legs_into_trips, which does its own sorting.
    """
    kept: List[ExtractedLeg] = []
    index_by_key: dict = {}

    for leg in legs:
        key = (
            leg.leg_type,
            leg.provider,
            leg.start_datetime,
            leg.end_datetime,
            leg.cost,
            leg.currency,
        )
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(kept)
            kept.append(leg)
            continue

        existing = kept[existing_index]
        primary, other = (
            (existing, leg) if len(existing.notes) >= len(leg.notes) else (leg, existing)
        )
        if other.confirmation_number and other.confirmation_number != primary.confirmation_number:
            extra = (
                f"Also referenced as confirmation {other.confirmation_number} "
                f"in a separate confirmation email for the same booking."
            )
            primary = replace(
                primary,
                notes=f"{primary.notes} {extra}".strip() if primary.notes else extra,
            )
        kept[existing_index] = primary

    return kept


def group_legs_into_trips(
    legs: Sequence[ExtractedLeg], gap_days: float = DEFAULT_GAP_DAYS
) -> List[List[ExtractedLeg]]:
    """Cluster legs into trips by date proximity.

    Legs are sorted by `start_datetime`, then walked in order: a new trip
    starts whenever the gap between the *immediately preceding* leg's
    `end_datetime` and the current leg's `start_datetime` exceeds
    `gap_days`. Otherwise the leg joins the trip in progress. Each returned
    trip is itself sorted chronologically by `start_datetime`.

    Known limitation - read before relying on this for anything automated:
    this is a pure date-proximity heuristic with no knowledge of actual
    itinerary structure (no shared destination, no "you flew home in
    between" signal). Two genuinely separate short trips that happen to
    fall within `gap_days` of each other (e.g. a weekend trip to Paris and,
    five days later, an unrelated one-day trip to Leeds) will be merged into
    a single "trip" in the output. This is an accepted tradeoff, not a bug:
    the rendered markdown makes a wrong grouping easy to spot at a glance,
    and nothing external or destructive happens as a result - re-running
    with a smaller --gap-days is the fix if it matters for a given batch.
    """
    if not legs:
        return []

    ordered = sorted(legs, key=lambda leg: leg.start_datetime)
    threshold = timedelta(days=gap_days)

    trips: List[List[ExtractedLeg]] = [[ordered[0]]]
    for leg in ordered[1:]:
        previous_leg = trips[-1][-1]
        gap = leg.start_datetime - previous_leg.end_datetime
        if gap > threshold:
            trips.append([leg])
        else:
            trips[-1].append(leg)

    return trips
