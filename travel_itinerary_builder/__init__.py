"""travel-itinerary-builder: turn plain-text travel confirmations into a
clean per-trip itinerary (markdown + .ics).

See README.md for the full pipeline. This package is phase 1 of 2: it reads
already-extracted plain text (a saved email body, a pasted confirmation,
etc). It does not read email, do OAuth, or integrate with any panel - that
is explicitly a separate, later phase.
"""
