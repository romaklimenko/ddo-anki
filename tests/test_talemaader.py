"""Exercise idiom imports and Anki output with invented source rows."""
from __future__ import annotations

import csv
import html
import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import httpx
import pytest

from ddo_anki import cli, deck, talemaader, talemaader_deck
from ddo_anki.talemaader import Talemaade, fetch_talemaader, load_talemaader, parse_talemaader
from ddo_anki.talemaader_deck import build_talemaader_deck, talemaade_to_note


HEADER = ("udtryk_id", "talemaade_udtryk", "ddo_definition")
SOURCE_FILENAME = "talemaader_leverance_1.csv"
SAMPLE = Talemaade("test-1", "pakke månen i papir", "forsøge noget opdigtet og umuligt")


def make_source(*entries: Talemaade) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t")
    writer.writerow(HEADER)
    writer.writerows((entry.expression_id, entry.expression, entry.definition) for entry in entries)
    return output.getvalue()


def make_archive(content: bytes, member: str = SOURCE_FILENAME) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    return buffer.getvalue()


def mock_download(monkeypatch: pytest.MonkeyPatch, content: bytes) -> list[str]:
    requested: list[str] = []

    def get(url: str, **kwargs: object) -> httpx.Response:
        requested.append(url)
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))

    monkeypatch.setattr(talemaader.httpx, "get", get)
    return requested


def test_parser_preserves_unicode_quotes_and_embedded_delimiters() -> None:
    entry = Talemaade(
        "test-ø",
        'lægge en "blå måne" i skuffen',
        'opdigtet forklaring med æ, ø og å; "citat"\tog en fane\nsamt en ny linje',
    )
    assert parse_talemaader("\ufeff" + make_source(entry)) == [entry]


def test_load_accepts_utf8_bom(tmp_path: Path) -> None:
    source = tmp_path / "local.csv"
    source.write_text(make_source(SAMPLE), encoding="utf-8-sig")
    assert load_talemaader(source) == [SAMPLE]


@pytest.mark.parametrize(
    "source",
    [
        "",
        " \n\t\n",
        "\t".join(HEADER) + "\n",
        "udtryk_id\ttalemaade_udtryk\nfake\topdigtet\n",
        "udtryk_id\ttalemaade_udtryk\tforkert_definition\nfake\topdigtet\tforkert\n",
        "udtryk_id,talemaade_udtryk,ddo_definition\nfake,opdigtet,forklaring\n",
        "udtryk_id\ttalemaade_udtryk\tddo_definition\tddo_definition\n"
        "fake\topdigtet\tforklaring\tanden forklaring\n",
    ],
)
def test_parser_rejects_empty_input_and_wrong_schema(source: str) -> None:
    with pytest.raises(ValueError):
        parse_talemaader(source)


@pytest.mark.parametrize(
    "row",
    [
        "\topdigtet\tforklaring\n",
        "fake\t \tforklaring\n",
        "fake\topdigtet\t\n",
        "fake\topdigtet\n",
        "fake\topdigtet\tforklaring\tekstra\n",
        'fake\t"opdigtet\tforklaring\n',
    ],
)
def test_parser_rejects_incomplete_or_malformed_rows(row: str) -> None:
    with pytest.raises(ValueError):
        parse_talemaader("\t".join(HEADER) + "\n" + row)


def test_parser_deduplicates_exact_rows_but_keeps_id_variants() -> None:
    variant = Talemaade(SAMPLE.expression_id, "folde månen sammen", SAMPLE.definition)
    other_id = Talemaade("test-2", SAMPLE.expression, SAMPLE.definition)
    assert parse_talemaader(make_source(SAMPLE, SAMPLE, variant, other_id)) == [
        SAMPLE,
        variant,
        other_id,
    ]


def test_parser_rejects_conflicting_definitions_for_one_identity() -> None:
    conflict = Talemaade(SAMPLE.expression_id, SAMPLE.expression, "en anden opdigtet betydning")
    with pytest.raises(ValueError):
        parse_talemaader(make_source(SAMPLE, conflict))


def test_ddo_search_url_encodes_expression_as_one_query() -> None:
    entry = Talemaade("test-3", 'månen & solen? "blå" #ø', "opdigtet forklaring")
    url = urlsplit(entry.source_url)
    assert url.scheme == "https"
    assert url.netloc == "ordnet.dk"
    assert url.path.startswith("/ddo/")
    assert parse_qs(url.query) == {"query": [entry.expression]}
    assert not url.fragment


def test_fetch_downloads_correct_zip_member_and_reuses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = mock_download(monkeypatch, make_archive(make_source(SAMPLE).encode("utf-8-sig")))
    cache_dir = tmp_path / "cache"

    assert fetch_talemaader(cache_dir) == [SAMPLE]
    assert load_talemaader(cache_dir / SOURCE_FILENAME) == [SAMPLE]
    assert fetch_talemaader(cache_dir) == [SAMPLE]
    assert len(requested) == 1
    assert requested[0].startswith("https://")


def test_refresh_replaces_cache_after_successful_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = tmp_path / SOURCE_FILENAME
    cache_file.write_text(make_source(SAMPLE), encoding="utf-8")
    updated = Talemaade(SAMPLE.expression_id, SAMPLE.expression, "en opdateret opdigtet forklaring")
    requested = mock_download(monkeypatch, make_archive(make_source(updated).encode("utf-8")))

    assert fetch_talemaader(tmp_path, refresh=True) == [updated]
    assert load_talemaader(cache_file) == [updated]
    assert len(requested) == 1


@pytest.mark.parametrize(
    "download",
    [
        b"not a zip archive",
        make_archive(b"irrelevant", member="talemaader_leverance_2.csv"),
        make_archive(b"wrong\tcolumns\ninvalid\tcontent\n"),
        make_archive(b"\xff\xfe\xfa"),
    ],
)
def test_failed_refresh_preserves_valid_cache(
    download: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_file = tmp_path / SOURCE_FILENAME
    original = make_source(SAMPLE).encode("utf-8")
    cache_file.write_bytes(original)
    mock_download(monkeypatch, download)

    with pytest.raises(ValueError):
        fetch_talemaader(tmp_path, refresh=True)

    assert cache_file.read_bytes() == original


def test_note_guid_survives_definition_edit_and_distinguishes_variants() -> None:
    edited = Talemaade(SAMPLE.expression_id, SAMPLE.expression, "en ny opdigtet forklaring")
    variant = Talemaade(SAMPLE.expression_id, "folde månen sammen", SAMPLE.definition)
    other_id = Talemaade("test-2", SAMPLE.expression, SAMPLE.definition)

    original_note = talemaade_to_note(SAMPLE)
    assert original_note.guid == talemaade_to_note(edited).guid
    assert len({original_note.guid, talemaade_to_note(variant).guid, talemaade_to_note(other_id).guid}) == 3


def test_package_has_separate_ids_one_card_per_idiom_and_escaped_attribution(
    tmp_path: Path,
) -> None:
    entry = Talemaade(
        "html-test",
        'gemme <månen> & "solen"',
        'opdigtet <script>alert("ø")</script> & forklaring',
    )
    out_path = tmp_path / "nested" / "talemaader.apkg"
    result = build_talemaader_deck([SAMPLE, entry], out_path, deck_name="Mine talemåder")
    assert result.apkg == out_path
    assert result.cards == 2
    assert result.audio_files == 0

    with ZipFile(out_path) as package:
        assert json.loads(package.read("media")) == {}
        database = tmp_path / "collection.anki2"
        database.write_bytes(package.read("collection.anki2"))

    with closing(sqlite3.connect(database)) as connection:
        models_json, decks_json = connection.execute("SELECT models, decks FROM col").fetchone()
        notes = connection.execute("SELECT mid, flds FROM notes").fetchall()
        cards = connection.execute("SELECT did, ord FROM cards").fetchall()

    models = json.loads(models_json)
    decks = json.loads(decks_json)
    assert len(notes) == len(cards) == 2
    assert talemaader_deck.MODEL_ID != deck.MODEL_ID
    assert talemaader_deck.DECK_ID != deck.DECK_ID
    assert {model_id for model_id, _ in notes} == {talemaader_deck.MODEL_ID}
    assert set(cards) == {(talemaader_deck.DECK_ID, 0)}
    assert str(deck.MODEL_ID) not in models
    assert str(deck.DECK_ID) not in decks

    model = models[str(talemaader_deck.MODEL_ID)]
    assert len(model["tmpls"]) == 1
    template = model["tmpls"][0]
    assert "{{Expression}}" in template["qfmt"]
    assert "{{Definition}}" not in template["qfmt"]
    assert "{{Definition}}" in template["afmt"]
    assert "{{Attribution}}" in template["afmt"]
    assert "{{LicenseURL}}" in template["afmt"]
    assert "{{DatasetURL}}" in template["afmt"]
    assert "{{SourceURL}}" in template["afmt"]
    assert "CC BY 4.0" in template["afmt"]
    assert decks[str(talemaader_deck.DECK_ID)]["name"] == "Mine talemåder"

    field_names = [field["name"] for field in model["flds"]]
    fields = [dict(zip(field_names, values.split("\x1f"), strict=True)) for _, values in notes]
    html_fields = next(note for note in fields if "&lt;månen&gt;" in note["Expression"])
    assert html_fields["Expression"] == html.escape(entry.expression)
    assert html_fields["Definition"] == html.escape(entry.definition)
    assert html.unescape(html_fields["SourceURL"]) == entry.source_url
    assert html.unescape(html_fields["DatasetURL"]) == talemaader.DATASET_URL
    assert html.unescape(html_fields["LicenseURL"]) == talemaader.LICENSE_URL
    assert html.unescape(html_fields["Attribution"]) == talemaader.ATTRIBUTION
    assert "creativecommons.org/licenses/by/4.0" in html_fields["LicenseURL"]


def test_cli_builds_from_local_source_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "local.csv"
    source.write_text(make_source(SAMPLE), encoding="utf-8")
    output = tmp_path / "local.apkg"

    def unexpected_download(*args: object, **kwargs: object) -> None:
        pytest.fail("A local source must not need a network request")

    monkeypatch.setattr(talemaader.httpx, "get", unexpected_download)
    assert cli.main(["talemaader-build", "--source", str(source), "-o", str(output)]) == 0
    assert output.is_file()


def test_cli_rejects_source_with_refresh(tmp_path: Path) -> None:
    source = tmp_path / "local.csv"
    source.write_text(make_source(SAMPLE), encoding="utf-8")
    output = tmp_path / "unexpected.apkg"
    try:
        result = cli.main(
            ["talemaader-build", "--source", str(source), "--refresh", "-o", str(output)]
        )
    except SystemExit as exc:
        result = exc.code
    assert result == 2
    assert not output.exists()
