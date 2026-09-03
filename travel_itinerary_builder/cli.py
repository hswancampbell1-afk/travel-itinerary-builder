"""CLI entry point.

Usage:
    python -m travel_itinerary_builder file1.txt file2.txt ... --output-dir OUT

Reads each input file as plain text, extracts one leg per file via the
Anthropic API (one call per file - never batched, matching the "one thing
per call" pattern the other siblings in this suite use), groups the
successfully-extracted legs into trips by date proximity, and writes one
markdown file plus one .ics file per detected trip into --output-dir. Every
unparseable/failed input is reported by name and reason in the final
summary - never silently dropped.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from .anthropic_client import DEFAULT_MODEL, build_client
from .extractor import ExtractedLeg, LegExtractionError, extract_leg
from .grouping import DEFAULT_GAP_DAYS, group_legs_into_trips
from .itinerary import render_trip_ics, render_trip_markdown, slug_for_trip


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="travel-itinerary-builder",
        description=(
            "Turn one or more plain-text travel booking confirmations into "
            "a clean itinerary (markdown + .ics), one per detected trip."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Plain-text confirmation files to extract (flight/hotel/car/train).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write <slug>.md and <slug>.ics files into.",
    )
    parser.add_argument(
        "--gap-days",
        type=float,
        default=DEFAULT_GAP_DAYS,
        help=(
            f"Max gap in days between one leg's end and the next leg's "
            f"start still considered part of the same trip "
            f"(default: {DEFAULT_GAP_DAYS})."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        help="Anthropic model id to use for extraction.",
    )
    return parser.parse_args(argv)


def _read_input(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def run(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill "
            "it in, or export it in your shell.",
            file=sys.stderr,
        )
        return 1

    client = build_client()

    legs: List[ExtractedLeg] = []
    failures: List[Tuple[str, str]] = []

    for path in args.inputs:
        try:
            text = _read_input(path)
        except OSError as exc:
            failures.append((path, f"could not read file: {exc}"))
            continue

        result = extract_leg(text, client, model=args.model)
        if isinstance(result, LegExtractionError):
            failures.append((path, result.reason))
        else:
            legs.append(result)

    os.makedirs(args.output_dir, exist_ok=True)

    trips = group_legs_into_trips(legs, gap_days=args.gap_days)

    written: List[Tuple[str, int]] = []
    for trip in trips:
        slug = slug_for_trip(trip)
        md_path = Path(args.output_dir) / f"{slug}.md"
        ics_path = Path(args.output_dir) / f"{slug}.ics"
        md_path.write_text(render_trip_markdown(trip), encoding="utf-8")
        ics_path.write_bytes(render_trip_ics(trip))
        written.append((slug, len(trip)))

    print(f"\n{len(trips)} trip(s) written to {args.output_dir}:")
    for slug, leg_count in written:
        print(f"  {slug}.md / {slug}.ics ({leg_count} leg(s))")

    if failures:
        print(f"\n{len(failures)} input(s) could not be used:")
        for path, reason in failures:
            print(f"  {path}: {reason}")
    else:
        print("\nAll inputs extracted successfully.")

    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
