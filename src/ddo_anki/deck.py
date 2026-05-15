"""Build an Anki .apkg from DDO entries via genanki."""
from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

import genanki

from .scraper import Entry, Meaning, Pronunciation

# Stable IDs - changing these breaks updates of decks already in users' Anki.
MODEL_ID = 1750000001
DECK_ID = 1750000002

MODEL = genanki.Model(
    MODEL_ID,
    "DDO - Danish (Ordnet.dk)",
    fields=[
        {"name": "Word"},
        {"name": "Type"},
        {"name": "Inflection"},
        {"name": "IPA"},
        {"name": "Audio"},
        {"name": "Meanings"},
        {"name": "Etymology"},
        {"name": "SourceURL"},
        {"name": "GUIDKey"},
    ],
    templates=[
        {
            "name": "Word -> Meanings",
            "qfmt": """
<div class="word">{{Word}}</div>
<div class="type">{{Type}}</div>
{{#Audio}}<div class="audio">{{Audio}}</div>{{/Audio}}
""",
            "afmt": """
{{FrontSide}}
<hr id="answer">
{{#IPA}}<div class="ipa">{{IPA}}</div>{{/IPA}}
{{#Inflection}}<div class="inflection"><span class="label">Bøjning</span> {{Inflection}}</div>{{/Inflection}}
<div class="meanings">{{Meanings}}</div>
{{#Etymology}}<div class="etymology"><span class="label">Oprindelse</span> {{Etymology}}</div>{{/Etymology}}
<div class="source"><a href="{{SourceURL}}">ordnet.dk</a></div>
""",
        }
    ],
    css="""
.card { font-family: -apple-system, "Segoe UI", sans-serif; font-size: 20px; color: #222; background: #fafafa; padding: 16px; text-align: left; }
.word { font-size: 36px; font-weight: 600; }
.type { color: #777; font-style: italic; margin-top: 4px; }
.audio { margin-top: 8px; }
.ipa { color: #555; margin: 8px 0 12px; }
.inflection, .etymology { color: #555; margin: 6px 0; }
.label { color: #999; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; margin-right: 6px; }
.meanings { margin-top: 12px; }
.meaning { margin: 6px 0; }
.meaning-num { color: #888; display: inline-block; min-width: 2.5em; }
.meaning-tag { color: #b03; font-size: 13px; text-transform: uppercase; margin-right: 4px; }
.examples { color: #555; font-style: italic; margin-left: 2.5em; font-size: 16px; }
.source { margin-top: 16px; font-size: 12px; color: #aaa; }
hr#answer { margin: 12px 0; border: 0; border-top: 1px solid #ddd; }
""",
)


@dataclass
class BuildResult:
    apkg: Path
    cards: int
    audio_files: int


def render_meanings(meanings: list[Meaning]) -> str:
    if not meanings:
        return ""
    parts = ["<ol class='meanings-list' style='padding-left:1.5em;margin:0;'>"]
    for m in meanings:
        tag = f"<span class='meaning-tag'>{html.escape(m.tag)}</span>" if m.tag else ""
        body = (
            f"<li class='meaning' value='{html.escape(m.number)}'>"
            f"<span class='meaning-num'>{html.escape(m.number)}</span>"
            f"{tag}{html.escape(m.text)}"
        )
        if m.examples:
            ex = " &nbsp;·&nbsp; ".join(html.escape(e) for e in m.examples)
            body += f"<div class='examples'>{ex}</div>"
        body += "</li>"
        parts.append(body)
    parts.append("</ol>")
    # We open with an ordered list but the explicit `value=` keeps DDO's
    # native 1 / 1.a / 1.b style numbering intact instead of forcing 1,2,3.
    return "".join(parts)


def render_audio(prons: list[Pronunciation], audio_files: dict[str, str]) -> str:
    """Anki uses [sound:filename.mp3] markers; only emit files we downloaded."""
    tags: list[str] = []
    for p in prons:
        if not p.audio_url:
            continue
        fname = audio_files.get(p.audio_url)
        if fname:
            tags.append(f"[sound:{fname}]")
    return " ".join(tags)


def render_ipa(prons: list[Pronunciation]) -> str:
    parts: list[str] = []
    for p in prons:
        if not p.ipa:
            continue
        chunk = f"[{html.escape(p.ipa)}]"
        if p.label:
            chunk = f"<span class='label'>{html.escape(p.label)}</span> {chunk}"
        parts.append(chunk)
    return " &nbsp; ".join(parts)


def entry_to_note(entry: Entry, audio_files: dict[str, str]) -> genanki.Note:
    word_html = html.escape(entry.word)
    if entry.homonym:
        word_html += f"<sup>{html.escape(entry.homonym)}</sup>"

    type_html = html.escape(entry.word_type) if entry.word_type else ""

    guid_key = f"ddo::{entry.word}::{entry.homonym}::{entry.word_type}"
    fields = [
        word_html,
        type_html,
        html.escape(entry.inflection),
        render_ipa(entry.pronunciations),
        render_audio(entry.pronunciations, audio_files),
        render_meanings(entry.meanings),
        html.escape(entry.etymology),
        entry.source_url,
        guid_key,
    ]
    return genanki.Note(
        model=MODEL,
        fields=fields,
        guid=genanki.guid_for(guid_key),
    )


def build_deck(
    entries: list[Entry],
    out_path: Path,
    *,
    deck_name: str = "Den Danske Ordbog",
    audio_files: dict[str, str] | None = None,
    audio_paths: list[Path] | None = None,
) -> BuildResult:
    audio_files = audio_files or {}
    audio_paths = audio_paths or []
    deck = genanki.Deck(DECK_ID, deck_name)
    for e in entries:
        deck.add_note(entry_to_note(e, audio_files))
    pkg = genanki.Package(deck)
    pkg.media_files = [str(p) for p in audio_paths]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_to_file(str(out_path))
    return BuildResult(apkg=out_path, cards=len(entries), audio_files=len(audio_paths))
