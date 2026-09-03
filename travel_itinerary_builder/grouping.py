"""Group successfully-extracted travel legs into trips by date proximity.

Nothing in this module talks to the network, the filesystem, or the
Anthropic API - it only takes a list of `ExtractedLeg` objects (already
extracted by extractor.py) and clusters them. Callers are responsible for
separating unparseable inputs out first: this module only ever sees legs
that extracted successfully, and never silently drops or guesses at one -
see cli.py for where unparseable inputs are collected and reported instead.
"""

from __future__ import annotations

from datetime import timedelta
from typing import List, Sequence

from .extractor import ExtractedLeg

# Default gap (in days) between one leg's end and the next leg's start that
# is still considered "the same trip". Exposed as a CLI flag (--gap-days)
# rather than hardcoded, since what counts as "still one trip" is a genuine
# judgment call that varies by traveler.
DEFAULT_GAP_DAYS = 4


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
