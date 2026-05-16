"""Tests for the bulk-scrape pruning / retry logic."""
from __future__ import annotations

import json
from pathlib import Path

from ddo_anki.bulk import is_retryable, prune_retryable, read_done_urls


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_is_retryable_classification() -> None:
    assert is_retryable({"status": 503})
    assert is_retryable({"status": 500})
    assert is_retryable({"status": 429})
    assert is_retryable({"status": -1, "error": "TimeoutException"})
    assert is_retryable({"status": 200, "error": "parse: x"})
    # Definitive outcomes - keep them
    assert not is_retryable({"status": 200, "entry": {"word": "hund"}})
    assert not is_retryable({"status": 404})
    assert not is_retryable({"status": 200, "entry": None})


def test_prune_retryable_drops_5xx_keeps_ok_and_404(tmp_path: Path) -> None:
    jsonl = tmp_path / "entries.jsonl"
    write_jsonl(
        jsonl,
        [
            {"url": "https://x/?query=a", "status": 200, "entry": {"word": "a"}},
            {"url": "https://x/?query=b", "status": 503, "error": ""},
            {"url": "https://x/?query=c", "status": 404},
            {"url": "https://x/?query=d", "status": 200, "entry": None},
            {"url": "https://x/?query=e", "status": -1, "error": "TimeoutException"},
        ],
    )
    stats = prune_retryable(jsonl)
    assert stats == {"kept": 3, "dropped": 2, "total_lines": 5}

    surviving_urls = read_done_urls(jsonl)
    assert surviving_urls == {
        "https://x/?query=a",
        "https://x/?query=c",
        "https://x/?query=d",
    }


def test_prune_retryable_uses_last_write_per_url(tmp_path: Path) -> None:
    jsonl = tmp_path / "entries.jsonl"
    # First attempt: 503. Second attempt: 200 ok. Last write should win.
    write_jsonl(
        jsonl,
        [
            {"url": "https://x/?query=a", "status": 503},
            {"url": "https://x/?query=a", "status": 200, "entry": {"word": "a"}},
        ],
    )
    stats = prune_retryable(jsonl)
    assert stats["dropped"] == 0
    assert read_done_urls(jsonl) == {"https://x/?query=a"}


def test_prune_on_missing_file_is_noop(tmp_path: Path) -> None:
    jsonl = tmp_path / "missing.jsonl"
    stats = prune_retryable(jsonl)
    assert stats == {"kept": 0, "dropped": 0, "total_lines": 0}
