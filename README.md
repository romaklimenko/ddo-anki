# ddo-anki

Build [Anki](https://apps.ankiweb.net/) decks from [Den Danske Ordbog](https://ordnet.dk/ddo) — Danish-vocabulary cards with native audio, IPA, full nested meanings, and a link back to the source entry.

- **Front:** word (with `¹`/`²` for homonyms) + grammatical type + playable audio.
- **Back:** IPA, inflection (`Bøjning`), all DDO meanings (preserving DDO's `1` / `1.a` / `1.b` / `2` numbering and tags like `overført` / `ASTROLOGI`), examples, etymology, link to ordnet.dk.

Audio mp3s are pulled from `static.ordnet.dk` and bundled inside the `.apkg`, so cards work fully offline once imported into Anki.

---

## Quick start (small wordlist)

```bash
uv sync                                                  # install deps + create .venv
uv run ddo-anki build words/sample.txt -o out/sample.apkg
# import out/sample.apkg into Anki: File -> Import
```

Re-running with the same wordlist reuses cached HTML in `cache/html/` and mp3s in `cache/audio/` — iteration is fast and ordnet.dk only sees one request per word.

## Full corpus (~104K cards)

DDO publishes an XML sitemap that lists every entry URL — **104,153** as of May 2026, with homonyms disambiguated as `query=hund,1`, `query=hund,2`, … This is the seed list for the full deck.

```bash
# 1. Pull the sitemap and dump all entry URLs
uv run python fetch_sitemap.py
cp cache/sitemap/ddo_all_urls.txt words/all_urls.txt

# 2. Async-scrape every URL into cache/entries.jsonl (resumable)
uv run ddo-anki bulk-scrape words/all_urls.txt --concurrency 5

# 3. (Optional) recover transient failures - see "Retrying transient failures" below
uv run ddo-anki bulk-retry words/all_urls.txt

# 4. Async-download every referenced mp3 into cache/audio/<shard>/
uv run ddo-anki bulk-audio

# 5. Build one big .apkg from the JSONL + audio cache
uv run ddo-anki bulk-build -o out/ddo-full.apkg
```

Each step is independently resumable: ctrl-C any time and re-run — the JSONL and the audio cache make every phase idempotent.

### Rough sizings

| | Count | Disk |
|---|---|---|
| Entry URLs in sitemap | 104,153 | — |
| Successfully parsed entries (one real run) | ~83,700 (~80%) | ~150 MB JSONL |
| Audio mp3s | ~150k unique | ~6 GB |
| Final `.apkg` | 1 file | ~5–8 GB |

### Realistic timings (home connection)

| Phase | Rate | Wall-clock |
|---|---|---|
| `bulk-scrape` @ `--concurrency 5` | ~5 req/s steady-state | ~4–6 h |
| `bulk-retry` @ `--concurrency 3` | ~3 req/s | minutes (only ~4 % of URLs) |
| `bulk-audio` @ `--concurrency 8` | ~100 dl/s (CDN) | ~30 min |
| `bulk-build` | — | ~5 min |

---

## Using the deck in Anki

### First import

**Anki Desktop** (Mac / Windows / Linux):

1. `File → Import` (or drag-and-drop the `.apkg` onto the main window).
2. Pick `out/ddo-full.apkg`. The import-options dialog appears — defaults are fine; just confirm **"Update existing notes when import conflicts"** is on (it is by default).
3. Click Import. For ~84k cards with audio, expect a **5–15 minute import** and the collection growing by ~5–8 GB.

**AnkiDroid / AnkiMobile:** copy the `.apkg` to the device, open it from your file manager, and it routes into Anki.

### Updating when new words arrive (or when you tweak the parser)

```bash
uv run python fetch_sitemap.py                       # refresh URL list (~1s)
cp cache/sitemap/ddo_all_urls.txt words/all_urls.txt
uv run ddo-anki bulk-scrape words/all_urls.txt       # resume - only NEW URLs fetched
uv run ddo-anki bulk-retry  words/all_urls.txt       # recover any 503s
uv run ddo-anki bulk-audio                           # only NEW mp3s downloaded
uv run ddo-anki bulk-build -o out/ddo-full.apkg      # rebuild deck
# then File -> Import the new .apkg in Anki Desktop
```

What happens in Anki on re-import (notes are keyed by a GUID derived from `word + homonym + word_type`):

- **Existing cards (same `word + homonym + word_type`):** fields are refreshed — updated meanings, fixed typos, new examples. **Your reviews, ease, intervals, due dates, and study stats are all preserved.**
- **New entries:** added as fresh cards in the "new" queue.
- **Removed entries** (rare — DDO occasionally retires obsolete entries): Anki's import only adds/updates; it never deletes. You'd have to delete them manually if you care. For a growing dictionary this is almost never an issue.

You can also use re-imports to iterate on the deck template / parser: rebuild + re-import, and your existing reviews carry over while the card content gets refreshed.

### Syncing through AnkiWeb (caveat)

AnkiWeb caps synced media at ~100 MB, far below our ~6 GB of mp3s. Three workable options:

- **Self-host an Anki sync server** (e.g., the official Rust `anki-sync-server`) — full sync including media. This is the cleanest path if you want audio on every device.
- **Sync without media** — cards sync to AnkiWeb / AnkiDroid / AnkiMobile, audio stays on your desktop only.
- **Build a no-audio variant** — skip `bulk-audio` before `bulk-build`, or use `--no-audio` for the small `build` flow. The resulting `.apkg` is a few hundred MB and syncs everywhere; cards have IPA and meanings but no playback.

---

## Subcommand reference

```
ddo-anki build        Build a deck from a small wordlist (POC).
ddo-anki bulk-scrape  Async-scrape every URL in a list into a JSONL.
ddo-anki bulk-retry   Re-scrape URLs whose JSONL record is a transient failure.
ddo-anki bulk-audio   Async-download every mp3 referenced by the JSONL.
ddo-anki bulk-build   Build one .apkg from a JSONL + audio cache.
```

### `build` (POC, small wordlists)

| Flag | Default | Notes |
|---|---|---|
| `wordlist` | required | text file, one Danish word per line; `#` for comments |
| `-o, --out` | `out/ddo.apkg` | output `.apkg` path |
| `--deck-name` | `Den Danske Ordbog` | Anki deck name |
| `--cache-dir` | `cache/html` | HTML cache (delete to force re-fetch) |
| `--audio-dir` | `cache/audio` | mp3 cache |
| `--no-audio` | off | skip mp3 downloads (faster, smaller deck) |
| `--delay` | `0.6` | min seconds between requests (synchronous mode) |

### `bulk-scrape`

| Flag | Default | Notes |
|---|---|---|
| `urls_file` | required | text file with one DDO URL per line (`cache/sitemap/ddo_all_urls.txt` from `fetch_sitemap.py`) |
| `--jsonl` | `cache/entries.jsonl` | append-only JSONL output, one record per URL |
| `--concurrency` | `5` | max in-flight requests |
| `--max-retries` | `5` | per-URL retry budget on 429/5xx with `Retry-After`-aware exponential backoff |
| `--log-every` | `200` | progress line every N URLs |
| `--limit` | `0` | process only the first N URLs (handy for smoke tests) |

### `bulk-retry`

Prunes records whose status is in `{408, 425, 429, 500, 502, 503, 504, network-error}` from the JSONL, then re-runs `bulk-scrape` — resume picks up only the dropped URLs.

| Flag | Default | Notes |
|---|---|---|
| `urls_file` | required | same URL list used for the original scrape |
| `--jsonl` | `cache/entries.jsonl` | the JSONL to prune + refill |
| `--concurrency` | `3` | gentler than `bulk-scrape` — retries hit a busy server |
| `--max-retries` | `8` | higher budget to ride out longer 503 windows |
| `--log-every` | `200` | progress line every N URLs |

Idempotent — run it again if a second wave is still flaky.

### `bulk-audio`

| Flag | Default | Notes |
|---|---|---|
| `--jsonl` | `cache/entries.jsonl` | source of audio URLs |
| `--audio-dir` | `cache/audio` | mp3 cache, sharded by `<first-4-chars>/<file>.mp3` |
| `--concurrency` | `8` | downloads hit `static.ordnet.dk` (CDN), so higher is fine |
| `--log-every` | `500` | progress line every N files |

### `bulk-build`

| Flag | Default | Notes |
|---|---|---|
| `--jsonl` | `cache/entries.jsonl` | parsed-entry source |
| `--audio-dir` | `cache/audio` | local mp3s — only files present on disk get attached |
| `-o, --out` | `out/ddo-full.apkg` | output `.apkg` path |
| `--deck-name` | `Den Danske Ordbog` | Anki deck name |

---

## What gets parsed

Each DDO entry yields a structured `Entry`:

- `word`, `homonym` (the superscript `¹` / `²`), `word_type` (e.g. `substantiv, intetkøn`)
- `inflection` (`-tet, -ter, -terne`)
- `etymology` (`Oprindelse`)
- `pronunciations`: list of `(label, ipa, audio_url)` — verbs commonly have 4–5 (`infinitiv`, `præsens`, `præteritum`, `præteritum participium`, …)
- `meanings`: list of `(number, text, tag, examples, citations)` — preserves DDO's `1`, `1.a`, `1.b`, `2`, `2.a`, `3` numbering and tags like `overført` or `ASTROLOGI`

---

## Failure handling and recovery

### What the bulk scraper does

- **Concurrency** is semaphore-capped (default 5). A real run averaged ~5 req/s steady-state.
- **Retry/backoff:** 429 and 5xx responses get exponential backoff that honors the `Retry-After` header.
- **Resume:** each URL produces exactly one JSONL line keyed by URL. Reruns skip URLs already present, so you can ctrl-C and pick up where you left off.
- **Permanent failures don't refetch:** `404`s and `entry: null` records are kept in the JSONL so they're treated as "done forever". Only transient failures are retryable.
- **Audio is sharded:** mp3s are stored as `cache/audio/<first-4-chars>/<id>.mp3` to keep any single directory under ~1k entries.

### What a real DDO run looks like

A full pass of the 104,153 URLs typically produces something like:

| | Count | Note |
|---|---|---|
| `ok` | ~83,700 | parsed entry stored |
| `http_404` | ~70 | stale sitemap entry — permanent, don't retry |
| `no_entry` | a handful | page returned 200 but contained no headword — permanent |
| `http_503` | ~4,200 | DDO server briefly overloaded — **retryable** |
| `http_500` | a handful | transient — **retryable** |

The ~4% transient-failure floor is normal and entirely recoverable with `bulk-retry`.

### Retrying transient failures

```bash
uv run ddo-anki bulk-retry words/all_urls.txt
```

Internally:

1. Walk the JSONL, classify every record. Anything in `{408, 425, 429, 5xx, network-error}` (or a 200 with a parse error) is **retryable**; everything else is kept.
2. Atomically rewrite the JSONL to drop just the retryable records.
3. Run the normal scrape — the resume logic picks up exactly those URLs.

Idempotent: each retry pass only touches URLs that are still failing. Run it as many times as you like.

> **Picking up new DDO entries / updating Anki:** see the **["Using the deck in Anki"](#using-the-deck-in-anki)** section above for the full re-import flow.

---

## Known limitations

- **Idioms / fixed expressions.** They appear inside an entry as further `match` spans but are not extracted as separate cards yet.
- **GUID ties to `word_type`.** If DDO ever changes a word's grammatical classification, that entry becomes a *new* note on the next import instead of an updated one. For ~104K entries this is negligible; if you care, swap the GUID basis to DDO's `entry_id`.
- **Single article per query.** For the small-wordlist `build` mode, DDO returns the primary homonym only. For the full-corpus mode this is a non-issue because the sitemap lists every homonym with `,N` disambiguation.
- **No fuzzy match.** If a word isn't found on DDO the CLI logs a warning and skips it.

---

## Polite-crawling notes

`https://ordnet.dk/robots.txt` blanket-disallows unknown bots, but ordnet.dk publishes the sitemap precisely for legitimate crawling. The pipeline ships a clear UA (`ddo-anki/0.1`), runs single-digit concurrency, and backs off on 429s/`Retry-After`. If you intend to redistribute the resulting deck, email DSL first — the dictionary data is © Det Danske Sprog- og Litteraturselskab.

---

## Project layout

```
src/ddo_anki/
  scraper.py       # httpx + BeautifulSoup parser for one DDO entry (sync)
  bulk.py          # async parallel scraper, audio downloader, prune/retry helpers
  deck.py          # genanki note model + .apkg writer
  cli.py           # ddo-anki command (build, bulk-scrape, bulk-retry, bulk-audio, bulk-build)
tests/
  test_scraper.py  # parser tests against saved HTML fixtures
  test_bulk.py     # tests for prune_retryable / is_retryable / resume
fetch_sitemap.py   # one-off helper: pull DDO's XML sitemap, write URL list
explore.py         # one-off helper: refresh samples/*.html fixtures
words/             # input word lists (sample.txt tracked; all_urls.txt gitignored)
samples/           # HTML fixtures used by tests (gitignored)
cache/             # html/, sitemap/, audio/, entries.jsonl (all gitignored)
out/               # built .apkg files (gitignored)
logs/              # run logs (gitignored)
```

`.gitignore` is deliberately strict: scraped DDO data is licensed and must not be committed. Only source code is tracked.

---

## Development

```bash
uv run -m pytest -q          # parser + bulk-pipeline tests
uv run python explore.py     # refetch & overwrite samples/*.html
```

---

## License

Source code: MIT.
Dictionary data: © Det Danske Sprog- og Litteraturselskab — see <https://ordnet.dk/ddo/om/kolofon>. The generated deck is for personal study; don't redistribute it without permission from DSL.
