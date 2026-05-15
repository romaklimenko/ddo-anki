"""Fetch and parse a single Den Danske Ordbog entry.

The DDO entry layout used here (verified against ordnet.dk in May 2026):

    <div class="artikel">
      <div class="definitionBoxTop">
        <span class="match">finit<span class="super">1</span></span>
        <span class="tekstmedium allow-glossing">substantiv, intetkøn</span>
      </div>
      <div id="id-boj">...inflection...</div>
      <div id="id-udt">...IPA + audio...</div>
      <div id="id-ety">...etymology...</div>
      <div id="content-betydninger">...nested .definitionBox[id^="betydning-"]...</div>
    </div>

Only the first matching entry on the page is parsed (which is the
primary match for the search query).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup, Tag

DDO_URL = "https://ordnet.dk/ddo/ordbog"
UA = "Mozilla/5.0 (compatible; ddo-anki/0.1; +https://github.com/local/ddo-anki)"


@dataclass
class Pronunciation:
    ipa: str
    label: str = ""  # e.g. "præsens", "præteritum", or "" for primary
    audio_url: str = ""


@dataclass
class Meaning:
    number: str            # "1", "1.a", "2.b" ...
    text: str              # the gloss itself
    tag: str = ""          # optional stempel like "overført", "ASTROLOGI"
    examples: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)


@dataclass
class Entry:
    query: str
    word: str
    homonym: str = ""              # the "1" / "2" superscript if any
    word_type: str = ""            # "substantiv, intetkøn", "verbum", ...
    inflection: str = ""
    etymology: str = ""
    pronunciations: list[Pronunciation] = field(default_factory=list)
    meanings: list[Meaning] = field(default_factory=list)
    source_url: str = ""

    @property
    def display_word(self) -> str:
        return f"{self.word}{self.homonym}" if self.homonym else self.word

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Entry":
        prons = [Pronunciation(**p) for p in d.get("pronunciations", [])]
        meanings = [Meaning(**m) for m in d.get("meanings", [])]
        return cls(
            query=d.get("query", ""),
            word=d["word"],
            homonym=d.get("homonym", ""),
            word_type=d.get("word_type", ""),
            inflection=d.get("inflection", ""),
            etymology=d.get("etymology", ""),
            pronunciations=prons,
            meanings=meanings,
            source_url=d.get("source_url", ""),
        )

    @property
    def short_type(self) -> str:
        """Compact tag like 'sb.', 'vb.', 'adj.'"""
        t = self.word_type.lower()
        if "substantiv" in t:
            return "sb."
        if "verbum" in t:
            return "vb."
        if "adjektiv" in t:
            return "adj."
        if "adverbium" in t:
            return "adv."
        if "pronomen" in t:
            return "pron."
        if "konjunktion" in t:
            return "konj."
        if "praeposition" in t or "præposition" in t:
            return "præp."
        if "interjektion" in t:
            return "interj."
        if "talord" in t or "numeral" in t:
            return "num."
        return self.word_type or "?"


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


class DdoScraper:
    """Polite HTTP client + parser for a single DDO entry."""

    def __init__(
        self,
        cache_dir: Path | str | None = "cache/html",
        delay: float = 0.6,
        timeout: float = 30.0,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": UA, "Accept-Language": "da,en;q=0.5"},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DdoScraper":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_html(self, query: str) -> str:
        slug = re.sub(r"[^a-z0-9æøåÆØÅ]+", "_", query.lower())
        cache_file = self.cache_dir / f"{slug}.html" if self.cache_dir else None
        if cache_file and cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

        self._sleep_if_needed()
        r = self._client.get(DDO_URL, params={"query": query})
        self._last_request = time.monotonic()
        r.raise_for_status()
        if cache_file:
            cache_file.write_text(r.text, encoding="utf-8")
        return r.text

    def lookup(self, query: str) -> Entry | None:
        html = self.fetch_html(query)
        entry = parse_entry(html, query=query)
        if entry:
            entry.source_url = f"{DDO_URL}?query={query}"
        return entry

    def lookup_many(self, queries: Iterable[str]) -> list[Entry]:
        out: list[Entry] = []
        for q in queries:
            try:
                e = self.lookup(q)
            except httpx.HTTPError as exc:
                print(f"[warn] {q!r}: HTTP error {exc!s}")
                continue
            if e is None:
                print(f"[warn] {q!r}: no entry found")
                continue
            out.append(e)
        return out

    def fetch_audio(self, url: str, dest_dir: Path) -> Path | None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = url.rsplit("/", 1)[-1]
        dest = dest_dir / name
        if dest.exists():
            return dest
        self._sleep_if_needed()
        try:
            r = self._client.get(url)
            self._last_request = time.monotonic()
            r.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"[warn] audio {url}: {exc!s}")
            return None
        dest.write_bytes(r.content)
        return dest

    def _sleep_if_needed(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)


def parse_entry(html: str, *, query: str = "") -> Entry | None:
    soup = BeautifulSoup(html, "lxml")
    artikel = soup.select_one("div.artikel")
    if artikel is None:
        return None

    top = artikel.select_one(".definitionBoxTop")
    if top is None:
        return None

    match = top.select_one("span.match")
    if match is None:
        return None

    super_span = match.select_one(".super")
    homonym = _clean(super_span.get_text()) if super_span else ""
    if super_span:
        super_span.extract()
    word = _clean(match.get_text())

    type_span = top.select_one(".tekstmedium.allow-glossing")
    word_type = _clean(type_span.get_text()) if type_span else ""

    inflection = _parse_box(artikel.select_one("#id-boj"))
    etymology = _parse_box(artikel.select_one("#id-ety"))
    pronunciations = _parse_pronunciations(artikel.select_one("#id-udt"))
    meanings = _parse_meanings(artikel.select_one("#content-betydninger"))

    return Entry(
        query=query,
        word=word,
        homonym=homonym,
        word_type=word_type,
        inflection=inflection,
        etymology=etymology,
        pronunciations=pronunciations,
        meanings=meanings,
    )


def _parse_box(box: Tag | None) -> str:
    if box is None:
        return ""
    # Drop the leading "stempel" label so we keep only the value.
    for stempel in box.select(".stempel"):
        stempel.extract()
    for tip in box.select(".tipIkon"):
        tip.extract()
    return _clean(box.get_text(" "))


def _parse_pronunciations(box: Tag | None) -> list[Pronunciation]:
    if box is None:
        return []
    # Drop tooltip icon and "Udtale" label - they're noise.
    for tag in box.select(".tipIkon, .stempel"):
        tag.extract()

    # `lydskrift` spans hold IPA + audio. Sibling `.diskret` spans before
    # a lydskrift label it (e.g. "præsens", "præteritum").
    pronunciations: list[Pronunciation] = []
    pending_label = ""
    container = box.select_one(".tekstmedium") or box
    for child in container.children:
        if not isinstance(child, Tag):
            continue
        classes = child.get("class") or []
        if "diskret" in classes:
            pending_label = _clean(child.get_text())
            continue
        if "lydskrift" in classes:
            audio = child.select_one("audio")
            audio_url = ""
            if audio is not None:
                a = audio.select_one("a[href]")
                if a is not None:
                    audio_url = str(a.get("href", ""))
            # IPA = text minus the audio/img machinery; strip the surrounding [].
            ipa_text = _clean(child.get_text())
            ipa_text = ipa_text.strip("[]").strip()
            pronunciations.append(
                Pronunciation(ipa=ipa_text, label=pending_label, audio_url=audio_url)
            )
            pending_label = ""
    return pronunciations


_BETYDNING_RE = re.compile(r"^betydning-(.+)$")


def _meaning_visible_number(box: Tag) -> str:
    """The displayed numbering (e.g. '1.', '1.a') sits as a sibling of the
    `definitionIndent` that wraps this box. Walk up and back to find it."""
    indent = box.parent
    if indent is None or "definitionIndent" not in (indent.get("class") or []):
        return ""
    sib = indent.find_previous_sibling("div", class_="definitionNumber")
    if sib is None:
        return ""
    return _clean(sib.get_text()).rstrip(".")


def _parse_meanings(container: Tag | None) -> list[Meaning]:
    if container is None:
        return []
    out: list[Meaning] = []
    for box in container.select('div.definitionBox[id^="betydning-"]'):
        bid = box.get("id", "")
        m = _BETYDNING_RE.match(str(bid))
        if not m:
            continue
        visible = _meaning_visible_number(box)
        number = visible or m.group(1).replace("-", ".")
        definition = box.select_one(".definition")
        if definition is None:
            continue
        tag_span = box.select_one(".stempelNoBorder")
        tag = _clean(tag_span.get_text()) if tag_span else ""

        # Examples / citations live as siblings of `box`, inside the same
        # `definitionIndent` wrapper - they apply to *this* meaning.
        examples: list[str] = []
        citations: list[str] = []
        parent = box.parent
        if parent is not None:
            for sib in parent.find_all("div", class_="definitionBox", recursive=False):
                if sib is box:
                    continue
                stempel = sib.select_one(".stempel")
                if stempel and _clean(stempel.get_text()) == "Eksempler":
                    inline = sib.select_one(".inlineList")
                    if inline:
                        parts = re.split(r"\s{2,}|  +", _clean(inline.get_text()))
                        examples.extend(p.strip() for p in parts if p.strip())
                citat = sib.select_one(".citat")
                if citat:
                    citations.append(_clean(citat.get_text()))

        out.append(
            Meaning(
                number=number,
                text=_clean(definition.get_text()),
                tag=tag,
                examples=examples,
                citations=citations,
            )
        )
    return out
