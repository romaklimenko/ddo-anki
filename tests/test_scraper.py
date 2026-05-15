"""Parser sanity tests using saved HTML samples."""
from __future__ import annotations

from pathlib import Path

from ddo_anki.scraper import parse_entry

SAMPLES = Path(__file__).parent.parent / "samples"


def _load(name: str) -> str:
    return (SAMPLES / f"{name}.html").read_text(encoding="utf-8")


def test_finit_noun() -> None:
    e = parse_entry(_load("finit"), query="finit")
    assert e is not None
    assert e.word == "finit"
    assert e.homonym == "1"
    assert "substantiv" in e.word_type
    assert e.inflection == "-tet, -ter, -terne"
    assert e.short_type == "sb."
    assert len(e.pronunciations) == 1
    p = e.pronunciations[0]
    assert "fi" in p.ipa
    assert p.audio_url.endswith(".mp3")
    assert len(e.meanings) >= 1
    assert e.meanings[0].text.startswith("verbum")


def test_smuk_adjective() -> None:
    e = parse_entry(_load("smuk"), query="smuk")
    assert e is not None
    assert e.word == "smuk"
    assert e.short_type == "adj."
    assert e.meanings, "expected at least one meaning"


def test_være_verb_has_multiple_audio() -> None:
    e = parse_entry(_load("være"), query="være")
    assert e is not None
    assert e.short_type == "vb."
    assert len(e.pronunciations) >= 4  # infinitiv + præsens + præteritum + ppt
    labels = [p.label for p in e.pronunciations]
    assert "præsens" in labels
    assert "præteritum" in labels


def test_hus_has_nested_meanings() -> None:
    e = parse_entry(_load("hus"), query="hus")
    assert e is not None
    numbers = [m.number for m in e.meanings]
    assert "1" in numbers
    assert any("." in n for n in numbers), f"expected sub-meanings, got {numbers}"


def test_hund_has_homonym_marker() -> None:
    e = parse_entry(_load("hund"), query="hund")
    assert e is not None
    assert e.homonym == "1"
