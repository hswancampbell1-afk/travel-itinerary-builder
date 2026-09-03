"""Thin wrapper around the Anthropic SDK client.

Kept deliberately minimal, the same shape as invoice-extractor's own
src/anthropic_client.py and the client-usage half of calendar-scheduler's
scheduler/nlp_parser.py: this is the only place that ever imports the
`anthropic` package's client class directly, and it does so lazily, so
importing this module - or extractor.py, which depends on it - never itself
requires ANTHROPIC_API_KEY to be set.

extractor.py depends on the narrow `AnthropicMessagesClient` protocol below,
not on this module's `build_client`, so tests can hand it any object shaped
like `anthropic.Anthropic().messages` (a mock) without needing a real key or
network access.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Protocol

# Two of the three existing siblings modelled here (calendar-scheduler's
# nlp_parser.py, meeting-notes-summariser's anthropic_client.py) default to
# claude-opus-5; invoice-extractor defaults to claude-sonnet-4-5. This module
# is built directly off calendar-scheduler's own forced-tool-use extraction
# pattern, so it matches that convention.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")


class AnthropicMessagesClient(Protocol):
    """The minimal client surface extractor.py depends on.

    Any object with a `.messages.create(**kwargs)` method matching the real
    `anthropic.Anthropic` client's shape satisfies this - including a mock in
    tests. Depending on this narrow protocol (not the SDK's concrete client
    class) is what lets the test suite run with no network access and no API
    key.
    """

    messages: Any


def build_client(
    client: Optional[AnthropicMessagesClient] = None,
) -> AnthropicMessagesClient:
    """Return `client` unchanged if given (dependency injection for tests),
    otherwise construct a real `anthropic.Anthropic()` client.

    No API key is read or validated here - the `anthropic` package itself
    reads ANTHROPIC_API_KEY from the environment when the real client is
    constructed, and raises its own clear error if it's missing.
    """
    if client is not None:
        return client
    from anthropic import Anthropic

    return Anthropic()
