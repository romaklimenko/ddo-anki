"""Load DSL's openly licensed Danish idioms and their DDO definitions."""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

DATASET_URL = "https://sprogteknologi.dk/dataset/1000-talemader-evalueringsdatasaet"
DOWNLOAD_URL = (
    "https://sprogtek-ressources.digst.govcloud.dk/"
    "1000%20danske%20talemaader%20og%20faste%20udtryk/talemaader_csv.zip"
)
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Det Danske Sprog- og Litteraturselskab (DSL), Den Danske Ordbog"
SOURCE_FILENAME = "talemaader_leverance_1.csv"
SOURCE_COLUMNS = ("udtryk_id", "talemaade_udtryk", "ddo_definition")


@dataclass(frozen=True)
class Talemaade:
    expression_id: str
    expression: str
    definition: str

    @property
    def source_url(self) -> str:
        # The published IDs do not reliably resolve to the corresponding idiom.
        return "https://ordnet.dk/ddo/ordbog?" + urlencode({"query": self.expression})


def parse_talemaader(text: str) -> list[Talemaade]:
    """Parse delivery 1's TSV, excluding the separate false-definition dataset."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter="\t", strict=True)
    try:
        header = next(reader, [])
        if len(header) != len(SOURCE_COLUMNS) or set(header) != set(SOURCE_COLUMNS):
            raise ValueError(
                "Expected delivery 1 TSV columns: " + ", ".join(SOURCE_COLUMNS)
            )

        entries: dict[tuple[str, str], Talemaade] = {}
        for row in reader:
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != len(header):
                raise ValueError(f"Line {reader.line_num}: expected three tab-separated fields")
            values = dict(zip(header, (value.strip() for value in row)))
            if not all(values.values()):
                raise ValueError(f"Line {reader.line_num}: ID, expression and definition are required")
            entry = Talemaade(
                expression_id=values["udtryk_id"],
                expression=values["talemaade_udtryk"],
                definition=values["ddo_definition"],
            )
            # The official release reuses one ID for two different expressions.
            key = (entry.expression_id, entry.expression)
            previous = entries.get(key)
            if previous is not None and previous != entry:
                raise ValueError(f"Line {reader.line_num}: conflicting definitions for {entry.expression!r}")
            entries[key] = entry
    except csv.Error as exc:
        raise ValueError(f"Invalid TSV near line {reader.line_num}: {exc}") from exc

    if not entries:
        raise ValueError("No expressions in the source file")
    return list(entries.values())


def load_talemaader(path: Path) -> list[Talemaade]:
    """Read an existing delivery 1 TSV without making network requests."""
    return parse_talemaader(path.read_text(encoding="utf-8-sig"))


def fetch_talemaader(cache_dir: Path, *, refresh: bool = False) -> list[Talemaade]:
    """Download the official archive once and cache only the correct definitions."""
    cache_path = cache_dir / SOURCE_FILENAME
    if cache_path.exists() and not refresh:
        return load_talemaader(cache_path)

    response = httpx.get(
        DOWNLOAD_URL,
        headers={"User-Agent": "ddo-anki/0.1"},
        timeout=60,
        follow_redirects=True,
    )
    response.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            data = archive.read(SOURCE_FILENAME)
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        raise ValueError(f"Official archive must contain {SOURCE_FILENAME}: {exc}") from exc

    entries = parse_talemaader(data.decode("utf-8-sig"))
    # Validate before replacing a usable cache. Read one named ZIP member only.
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
        temporary_path.replace(cache_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return entries
