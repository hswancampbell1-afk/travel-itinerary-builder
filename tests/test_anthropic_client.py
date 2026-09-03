"""A light test for the client-injection boundary - no network, no API key."""

from travel_itinerary_builder.anthropic_client import build_client


class _FakeClient:
    pass


def test_build_client_returns_injected_client_unchanged():
    fake = _FakeClient()

    assert build_client(fake) is fake
