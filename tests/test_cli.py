"""End-to-end CLI tests with a mocked Anthropic client - no network, no key.

`cli.build_client` is monkeypatched directly (the same name the module
imported), so `cli.run(...)` exercises the real read-extract-group-write
pipeline against real temp files, with only the API call itself faked.
"""

from types import SimpleNamespace

import pytest

from travel_itinerary_builder import cli
from travel_itinerary_builder.extractor import TOOL_NAME

FLIGHT_INPUT = {
    "unparseable": False,
    "reason": "",
    "leg_type": "flight",
    "provider": "British Airways",
    "confirmation_number": "XJ7K2P",
    "traveler_name": "Harrison Campbell",
    "start_datetime": "2026-06-12T10:15:00",
    "start_location": "LHR",
    "end_datetime": "2026-06-12T13:05:00",
    "end_location": "JFK",
    "cost": 412.5,
    "currency": "GBP",
    "notes": "",
}

NEWSLETTER_INPUT = {
    "unparseable": True,
    "reason": "This is a marketing newsletter, not a booking confirmation.",
    "leg_type": "other",
    "provider": "",
    "confirmation_number": "",
    "traveler_name": "",
    "start_datetime": "2026-01-01",
    "start_location": "",
    "end_datetime": "2026-01-01",
    "end_location": "",
    "cost": 0,
    "currency": "",
    "notes": "",
}


def make_tool_use_block(tool_input: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=TOOL_NAME, input=tool_input)


def make_message(*blocks) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    # cli.run only checks that ANTHROPIC_API_KEY is *set* before doing
    # anything - the actual client is always the monkeypatched fake below.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")


def test_cli_end_to_end_writes_real_files(tmp_path, monkeypatch):
    flight_file = tmp_path / "flight.txt"
    flight_file.write_text("Flight confirmation text", encoding="utf-8")

    fake_client = FakeClient([make_message(make_tool_use_block(FLIGHT_INPUT))])
    monkeypatch.setattr(cli, "build_client", lambda: fake_client)

    output_dir = tmp_path / "out"
    exit_code = cli.run([str(flight_file), "--output-dir", str(output_dir)])

    assert exit_code == 0
    md_files = list(output_dir.glob("*.md"))
    ics_files = list(output_dir.glob("*.ics"))
    assert len(md_files) == 1
    assert len(ics_files) == 1
    assert md_files[0].stat().st_size > 0
    assert ics_files[0].stat().st_size > 0
    assert "British Airways" in md_files[0].read_text(encoding="utf-8")


def test_cli_reports_unparseable_input_in_stdout_not_silently(
    tmp_path, monkeypatch, capsys
):
    flight_file = tmp_path / "flight.txt"
    flight_file.write_text("Flight confirmation text", encoding="utf-8")
    newsletter_file = tmp_path / "newsletter.txt"
    newsletter_file.write_text("50% off flights this week only!", encoding="utf-8")

    fake_client = FakeClient(
        [
            make_message(make_tool_use_block(FLIGHT_INPUT)),
            make_message(make_tool_use_block(NEWSLETTER_INPUT)),
        ]
    )
    monkeypatch.setattr(cli, "build_client", lambda: fake_client)

    output_dir = tmp_path / "out"
    exit_code = cli.run(
        [str(flight_file), str(newsletter_file), "--output-dir", str(output_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "1 input(s) could not be used" in captured.out
    assert str(newsletter_file) in captured.out
    assert "newsletter" in captured.out.lower()
    # The good input still produced a trip despite the other one failing.
    assert len(list(output_dir.glob("*.md"))) == 1


def test_cli_missing_api_key_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    flight_file = tmp_path / "flight.txt"
    flight_file.write_text("Flight confirmation text", encoding="utf-8")

    exit_code = cli.run(
        [str(flight_file), "--output-dir", str(tmp_path / "out")]
    )

    assert exit_code == 1
