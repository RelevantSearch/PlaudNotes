"""Tests for the modern S3/content_list transcript + summary path.

Empirical findings (Stefan, 2026-05-02):
  - Transcript segments use content/start_time/end_time, NOT text/start_ms/bg
  - Summaries are sometimes raw markdown, sometimes JSON-wrapped with
    {"ai_content": "<markdown>"}
"""

from __future__ import annotations

import gzip
import json
from unittest.mock import MagicMock, patch

import pytest

from plaud_notes_mcp.plaud_client import PlaudClient, TranscriptSegment


@pytest.fixture
def client():
    return PlaudClient(token="eyJ.test.x", region="us")


def test_segment_from_api_recognizes_modern_keys():
    seg = TranscriptSegment.from_api(
        {
            "start_time": 50,
            "end_time": 29990,
            "content": "Hello world",
            "speaker": "Speaker 1",
        }
    )
    assert seg.text == "Hello world"
    assert seg.speaker == "Speaker 1"
    assert seg.start_ms == 50
    assert seg.end_ms == 29990


def test_segment_from_api_legacy_keys_still_work():
    # Legacy API shape — start_time_ms / end_time_ms (not start_ms/end_ms,
    # those are dataclass field names, not API keys).
    seg = TranscriptSegment.from_api(
        {"start_time_ms": 100, "end_time_ms": 200, "text": "legacy", "spk": "A"}
    )
    assert seg.text == "legacy"
    assert seg.speaker == "A"
    assert seg.start_ms == 100
    assert seg.end_ms == 200


def test_segment_from_api_oldest_legacy_keys():
    # Oldest fallback: bg/ed
    seg = TranscriptSegment.from_api({"bg": 50, "ed": 150, "text": "ancient", "spk": "X"})
    assert seg.start_ms == 50
    assert seg.end_ms == 150


def test_unwrap_summary_raw_markdown_passes_through():
    raw = "# Heading\n\nBody text."
    assert PlaudClient._unwrap_summary(raw) == raw


def test_unwrap_summary_json_wrapped_with_ai_content():
    payload = json.dumps({"ai_content": "# Heading\n\nBody text."})
    assert "Heading" in PlaudClient._unwrap_summary(payload)


def test_unwrap_summary_json_wrapped_with_alternate_keys():
    for key in ("content", "summary", "text"):
        payload = json.dumps({key: "the markdown"})
        assert PlaudClient._unwrap_summary(payload) == "the markdown"


def test_unwrap_summary_invalid_json_returns_raw():
    raw = "{ not json"
    assert PlaudClient._unwrap_summary(raw) == raw


def test_find_content_link_priority_order():
    detail = {
        "content_list": [
            {"data_type": "transaction", "data_link": "https://s3/old"},
            {"data_type": "transaction_polish", "data_link": "https://s3/new"},
        ]
    }
    # transaction_polish preferred over transaction
    assert (
        PlaudClient._find_content_link(detail, ("transaction_polish", "transaction"))
        == "https://s3/new"
    )


def test_find_content_link_falls_back_when_preferred_missing():
    detail = {
        "content_list": [{"data_type": "transaction", "data_link": "https://s3/old"}]
    }
    assert (
        PlaudClient._find_content_link(detail, ("transaction_polish", "transaction"))
        == "https://s3/old"
    )


def test_find_content_link_none_when_missing():
    assert PlaudClient._find_content_link({"content_list": []}, ("transaction",)) is None
    assert PlaudClient._find_content_link({}, ("transaction",)) is None


def test_get_transcript_modern_s3_path(client):
    detail = {
        "content_list": [
            {"data_type": "transaction_polish", "data_link": "https://s3/transcript"}
        ]
    }
    payload = json.dumps(
        {
            "segments": [
                {"start_time": 0, "end_time": 1000, "content": "First", "speaker": "A"},
                {"start_time": 1000, "end_time": 2000, "content": "Second", "speaker": "B"},
            ]
        }
    )
    gzipped = gzip.compress(payload.encode("utf-8"))

    with patch.object(client, "get_recording_detail", return_value=detail), patch.object(
        PlaudClient, "_fetch_s3_content", return_value=payload
    ):
        transcript = client.get_transcript("a" * 32)

    assert len(transcript.segments) == 2
    assert transcript.segments[0].text == "First"
    assert transcript.segments[1].speaker == "B"


def test_get_transcript_legacy_fallback(client):
    detail = {
        "content_list": [],
        "trans_result": {"segments": [{"text": "legacy", "speaker": "A"}]},
    }
    with patch.object(client, "get_recording_detail", return_value=detail):
        transcript = client.get_transcript("a" * 32)
    assert len(transcript.segments) == 1
    assert transcript.segments[0].text == "legacy"


def test_get_summary_modern_s3_path_json_wrapped(client):
    detail = {
        "content_list": [{"data_type": "auto_sum_note", "data_link": "https://s3/summary"}]
    }
    with patch.object(client, "get_recording_detail", return_value=detail), patch.object(
        PlaudClient, "_fetch_s3_content", return_value=json.dumps({"ai_content": "the summary"})
    ):
        assert client.get_summary("a" * 32) == "the summary"


def test_get_summary_modern_s3_path_raw_markdown(client):
    detail = {
        "content_list": [{"data_type": "auto_sum_note", "data_link": "https://s3/summary"}]
    }
    with patch.object(client, "get_recording_detail", return_value=detail), patch.object(
        PlaudClient, "_fetch_s3_content", return_value="# Heading\n\nBody"
    ):
        assert "Heading" in client.get_summary("a" * 32)


def test_get_summary_legacy_fallback(client):
    detail = {"content_list": [], "ai_content": "legacy summary"}
    with patch.object(client, "get_recording_detail", return_value=detail):
        assert client.get_summary("a" * 32) == "legacy summary"


def test_get_transcript_s3_failure_falls_back(client):
    detail = {
        "content_list": [
            {"data_type": "transaction", "data_link": "https://s3/broken"}
        ],
        "trans_result": {"segments": [{"text": "fallback", "speaker": "A"}]},
    }
    with patch.object(client, "get_recording_detail", return_value=detail), patch.object(
        PlaudClient, "_fetch_s3_content", return_value=""  # simulated fetch failure
    ):
        transcript = client.get_transcript("a" * 32)
    assert len(transcript.segments) == 1
    assert transcript.segments[0].text == "fallback"
