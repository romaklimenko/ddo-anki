"""Build an Anki deck of Danish idioms from DSL's evaluation dataset."""
from __future__ import annotations

import html
from pathlib import Path

import genanki

from .deck import BuildResult
from .talemaader import ATTRIBUTION, DATASET_URL, LICENSE_URL, Talemaade

# Stable IDs keep this collection separate from the DDO word collection.
MODEL_ID = 1750000101
DECK_ID = 1750000102

MODEL = genanki.Model(
    MODEL_ID,
    "Danske talemåder (DSL)",
    fields=[
        {"name": "Expression"},
        {"name": "Definition"},
        {"name": "SourceURL"},
        {"name": "DatasetURL"},
        {"name": "LicenseURL"},
        {"name": "Attribution"},
        {"name": "GUIDKey"},
    ],
    templates=[
        {
            "name": "Talemåde -> Betydning",
            "qfmt": '<div class="expression">{{Expression}}</div>',
            "afmt": """
{{FrontSide}}
<hr id="answer">
<div class="definition">{{Definition}}</div>
<div class="source">
{{Attribution}}<br>
<a href="{{SourceURL}}">Slå op i DDO</a> ·
<a href="{{DatasetURL}}">1000 talemåder</a> ·
<a href="{{LicenseURL}}">CC BY 4.0</a>
</div>
""",
        }
    ],
    css="""
.card { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 22px; line-height: 1.5; color: #222; background: #fafafa; padding: 20px; text-align: left; }
.expression { font-size: 30px; font-weight: 600; white-space: pre-wrap; }
.definition { white-space: pre-wrap; }
.source { margin-top: 24px; font-size: 12px; color: #666; }
a { color: #2463a0; }
hr#answer { margin: 16px 0; border: 0; border-top: 1px solid #ccc; }
.nightMode.card, .night_mode.card { color: #eee; background: #222; }
.nightMode .source, .night_mode .source { color: #bbb; }
.nightMode a, .night_mode a { color: #8bbfff; }
.nightMode hr#answer, .night_mode hr#answer { border-color: #666; }
""",
)


def talemaade_to_note(entry: Talemaade) -> genanki.Note:
    """Preserve source wording and keep note identity independent of definitions."""
    guid_key = f"dsl-talemaader::{entry.expression_id}::{entry.expression}"
    fields = [
        entry.expression,
        entry.definition,
        entry.source_url,
        DATASET_URL,
        LICENSE_URL,
        ATTRIBUTION,
        guid_key,
    ]
    return genanki.Note(
        model=MODEL,
        fields=[html.escape(value) for value in fields],
        guid=genanki.guid_for(guid_key),
    )


def build_talemaader_deck(
    entries: list[Talemaade],
    out_path: Path,
    *,
    deck_name: str = "Danske talemåder",
) -> BuildResult:
    description = (
        "Talemåder med danske betydningsforklaringer fra Den Danske Ordbog. "
        f"{html.escape(ATTRIBUTION)}. "
        f'<a href="{html.escape(DATASET_URL)}">1000 talemåder</a>. '
        f'<a href="{html.escape(LICENSE_URL)}">CC BY 4.0</a>. '
        "Konverteret til Anki. Kildens ordlyd er uændret."
    )
    deck = genanki.Deck(DECK_ID, deck_name, description=description)
    for entry in entries:
        deck.add_note(talemaade_to_note(entry))
    package = genanki.Package(deck)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(out_path))
    return BuildResult(apkg=out_path, cards=len(entries), audio_files=0)
