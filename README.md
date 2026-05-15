# ddo-anki

Build [Anki](https://apps.ankiweb.net/) decks from [Den Danske Ordbog](https://ordnet.dk/ddo).

- **Front:** word + grammatical type + audio button.
- **Back:** IPA, inflection, all DDO meanings (with examples), etymology, link to the source entry.

Audio mp3s are pulled from `static.ordnet.dk` and bundled inside the `.apkg`, so cards work fully offline once imported into Anki.

## Quick start

```bash
# Install dependencies and create the .venv
uv sync

# Build a deck from a wordlist (one Danish word per line)
uv run ddo-anki words/sample.txt -o out/sample.apkg

# Import out/sample.apkg into Anki (File -> Import)
```

Re-running with the same wordlist reuses cached HTML in `cache/html/` and mp3s in `cache/audio/`, so iteration is fast and ordnet.dk only sees one request per word.

### Useful flags

| Flag | Default | Notes |
|---|---|---|
| `-o, --out` | `out/ddo.apkg` | output `.apkg` path |
| `--deck-name` | `Den Danske Ordbog` | name shown in the Anki sidebar |
| `--cache-dir` | `cache/html` | HTML cache (delete to force re-fetch) |
| `--audio-dir` | `cache/audio` | mp3 cache |
| `--no-audio` | off | skip mp3 downloads (faster, smaller deck) |
| `--delay` | `0.6` | minimum seconds between HTTP requests |

## Project layout

```
src/ddo_anki/
  scraper.py   # httpx + BeautifulSoup parser for one DDO entry
  deck.py      # genanki note model + .apkg writer
  cli.py       # ddo-anki command
tests/
  test_scraper.py   # parser tests against saved HTML fixtures
samples/     # HTML fixtures used by tests (gitignored after the first commit, regenerate with explore.py)
words/       # input word lists
cache/       # HTML + audio cache (gitignored)
out/         # built .apkg files (gitignored)
```

## What gets parsed

Each DDO entry yields a structured `Entry`:

- `word`, `homonym` (the superscript `¹` / `²`), `word_type` (e.g. `substantiv, intetkøn`)
- `inflection` (`-tet, -ter, -terne`)
- `etymology` (`Oprindelse`)
- `pronunciations`: list of `(label, ipa, audio_url)` — verbs commonly have 4–5 (infinitiv, præsens, præteritum, præteritum participium, …)
- `meanings`: list of `(number, text, tag, examples, citations)` — preserves DDO's `1`, `1.a`, `1.b`, `2`, `2.a`, `3` numbering and tags like `overført` or `ASTROLOGI`

## Known limitations

- **Homonyms.** A DDO search returns the primary entry only. `spise` resolves to `spise¹` (the noun "meal"), not `spise²` (the verb). To target a specific homonym you'd need `entry_id=...` in the URL. For most everyday vocabulary the primary entry is the one you want; for ambiguous lemmas (sub-1% of the corpus) we'd want a second pass.
- **Idioms / fixed expressions.** They appear inside an entry as further `match` spans but are not extracted as separate cards yet.
- **No fuzzy match.** If a word isn't found on DDO the CLI logs a warning and skips it. Plug `--no-audio` to keep iteration cheap while you clean up a wordlist.

## Scraping the full ~104K-word DDO corpus

DDO publishes an XML sitemap (`https://ordnet.dk/sitemap_index.xml`) that lists every entry URL — **104,153** as of May 2026, with homonyms disambiguated as `query=hund,1`, `query=hund,2`, …

### Three-step pipeline

```bash
# 1. Pull the sitemap and dump all entry URLs to cache/sitemap/ddo_all_urls.txt
uv run python fetch_sitemap.py
cp cache/sitemap/ddo_all_urls.txt words/all_urls.txt

# 2. Async-scrape every URL into cache/entries.jsonl (resumable)
uv run ddo-anki bulk-scrape words/all_urls.txt --concurrency 6

# 3. Async-download every referenced mp3 into cache/audio/<shard>/
uv run ddo-anki bulk-audio

# 4. Build one .apkg from the JSONL + audio cache
uv run ddo-anki bulk-build -o out/ddo-full.apkg
```

### What the bulk scraper does

- **Concurrency:** semaphore-capped (default 5; observed ~20 req/s with concurrency 6 on a home connection).
- **Retry/backoff:** 429 and 5xx responses get an exponential backoff that honors the `Retry-After` header.
- **Resume:** each URL produces exactly one JSONL line, keyed by URL. Reruns skip URLs already present, so you can ctrl-C and pick up where you left off.
- **Failures don't refetch:** a 404 or parse error gets recorded as a JSONL line with `entry: null`, so the run never retries hopeless URLs.
- **Audio is sharded:** mp3s are stored as `cache/audio/<first-4-chars>/<id>.mp3` to keep any single directory under ~1k entries.

### Rough sizings

| | Count | Disk |
|---|---|---|
| Entry URLs | 104,153 | — |
| JSONL after scrape | ~104k lines | ~150 MB |
| Audio mp3s | ~150k unique | ~6 GB |
| Final `.apkg` | 1 file | ~5–8 GB |

### Polite-crawling notes

`https://ordnet.dk/robots.txt` blanket-disallows unknown bots but ordnet.dk publishes the sitemap precisely for legitimate crawling. The pipeline ships a clear UA (`ddo-anki/0.1`), runs single-digit-concurrency, and backs off on 429s. If you intend to redistribute the resulting deck, email DSL first — the dictionary data is © Det Danske Sprog- og Litteraturselskab.

## Development

```bash
uv run -m pytest -q          # parser tests against saved HTML fixtures
uv run python explore.py     # refetch & overwrite samples/*.html
```

## License

Source code: MIT.
Dictionary data: © Det Danske Sprog- og Litteraturselskab — see <https://ordnet.dk/ddo/om/kolofon>. The generated deck is for personal study; don't redistribute it without permission from DSL.
