# travel-itinerary-builder

Turn one or more plain-text travel booking confirmations (flight, hotel, car
rental, or train - copy-pasted email bodies, not PDFs or screenshots) into a
clean itinerary: one markdown file plus one standard `.ics` calendar file
per detected trip.

This is one of a set of independent portfolio tools demonstrating practical
AI-assisted automation for admin/travel-coordination work.

**Phase 1 of 2.** This tool reads plain text you already have (a saved email
body, a pasted confirmation, etc). It does not read email, do OAuth, or
plug into any panel - a thin wrapper that reads a real inbox is a separate,
later phase, once this piece is proven standalone.

## What it does

1. For each input file, sends the raw text to Claude (Anthropic API) with a
   forced tool-use call and asks it to extract exactly one travel "leg":
   type (flight/hotel/car/train), provider, confirmation number, traveler,
   start/end date-time and location, cost, and notes - or to flag the text
   as not a booking confirmation at all (a newsletter, an unrelated
   receipt), rather than force-fitting it into a fake leg.
2. Collapses legs that describe the same real-world booking twice (e.g. an
   initial "confirmation" and a later "hotel details" follow-up email for
   one hotel stay), then groups what's left into trips by date proximity (a
   new trip starts whenever the gap between one leg's end and the next
   leg's start exceeds `--gap-days`, default 4).
3. Writes one `<slug>.md` and one `<slug>.ics` file per trip into
   `--output-dir`.
4. Prints a summary: how many trips were written, and - never silently -
   every input that couldn't be used, with the reason.

## Project structure

```
travel-itinerary-builder/
├── travel_itinerary_builder/
│   ├── anthropic_client.py   # thin client wrapper - the only module that
│   │                         # imports the anthropic package directly
│   ├── extractor.py          # forced tool-use extraction, one leg per call
│   ├── grouping.py           # pure date-proximity grouping into trips
│   ├── itinerary.py          # renders one trip to markdown + .ics
│   └── cli.py                # ties the above together
├── tests/
├── sample_data/               # two example confirmation texts to try the CLI on
├── requirements.txt
├── .env.example
└── .gitignore
```

`extractor.py`, `grouping.py`, and `itinerary.py` never import the
`anthropic` package - `extract_leg()` takes an already-constructed client as
a parameter (dependency injection), so the test suite runs with no network
access and no API key at all; only `anthropic_client.build_client()` (used
by the real CLI, not by tests) ever constructs a live client.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Get a key from the [Anthropic Console](https://console.anthropic.com/settings/keys).

## Running it

```bash
python -m travel_itinerary_builder sample_data/flight_confirmation.txt sample_data/hotel_confirmation.txt --output-dir out
```

Options:

- `--output-dir OUT` (required) - where `<slug>.md` / `<slug>.ics` are written.
- `--gap-days N` - max days between one leg's end and the next leg's start
  still counted as the same trip (default 4).
- `--model MODEL` - Anthropic model id (default `$ANTHROPIC_MODEL` or
  `claude-opus-5`).

## Running the tests

No API key or network needed - every test mocks the Anthropic client.

```bash
python -m pytest -q
```

## Limitations

- **Text input only.** PDFs, screenshots, and images of confirmations are
  explicitly out of scope this version - paste or export the confirmation
  as plain text first.
- **The trip-grouping heuristic is date-proximity only, nothing more.** It
  has no idea what a "trip" structurally is - no shared destination check,
  no "you flew home in between" signal. Two genuinely separate short trips
  that happen to fall within `--gap-days` of each other (a weekend in
  Paris, then five days later an unrelated day trip to Leeds) will be
  merged into one output trip. This is an accepted tradeoff of a simple
  heuristic, not a bug: the markdown output makes a wrong grouping easy to
  spot at a glance, and nothing external or destructive happens as a
  result - re-run with a smaller `--gap-days` if it matters for a given
  batch.
- **A date-only hotel check-in is ordered as if it were 15:00** (an ordinary
  global check-in convention), so it renders after a same-day timed flight
  rather than before it. This is a display-ordering nicety only - the
  displayed text and the `.ics` event both still show the leg as date-only,
  never a fabricated time. Any OTHER date-only leg type (a car or train with
  no stated time - unusual, since those normally carry one) sorts at midday
  as a neutral placeholder instead.
- **`.ics` files are a courtesy export, not verified against every calendar
  app.** They're built with the `icalendar` package and validated by
  parsing them back in this project's own tests, but real-world calendar
  clients (Outlook, Google Calendar, Apple Calendar, ...) each have their
  own quirks reading third-party `.ics` files - if an event looks wrong
  after importing, check the source markdown first.
- **Duplicate-booking detection is exact-match only.** Two legs are folded
  into one when leg_type, provider, start/end datetime, cost and currency
  all match exactly (confirmation_number is deliberately excluded, since the
  same booking is often quoted under two different reference numbers across
  its own confirmation emails). A genuine duplicate that differs in any of
  those other fields - a slightly reworded provider name, a rounding
  difference in cost - will not be caught and will still appear as two legs.
- **One API call per input file, no batching.** Matches the "one thing per
  call" pattern used across this suite's other extraction tools - it costs
  more calls for a large batch of confirmations, in exchange for one bad
  input never being able to corrupt another's extraction.
- **No retry/repair loop.** If the model's structured output fails
  validation, that one input is reported as failed and the tool moves on -
  it is not automatically re-prompted to fix itself.
