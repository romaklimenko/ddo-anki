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

## Scaling to the full ~90K-word DDO corpus

This POC is built so the heavy step (scraping + audio download) is the only thing that has to scale. A plan that should work without irritating ordnet.dk:

1. **Get a seed wordlist.** DDO doesn't expose a public sitemap, but reasonable options:
   - **Frequency-ordered list** from KorpusDK or the Leipzig Danish corpus — best vocabulary value per card. Start with the top 5k / 20k / 50k and stop wherever cards stop being useful.
   - **Hunspell `da_DK.dic`** — ~250k forms, broader than DDO; ~30 % won't resolve, which is fine.
   - **DDO's A–Å index pages** (`https://ordnet.dk/ddo/leksikon/...`) — paginated, scrapeable to extract `entry_id` for every headword.
2. **Cache and parallelise.** The current `DdoScraper` already caches and rate-limits per-instance. For a full run:
   - Spawn ~4 worker processes, each with its own scraper and `delay=0.5–1.0` — effective load ~5–8 req/s.
   - Estimated wall time: ~5–8 h for HTML, similar again for audio. Each entry is ~5–50 KB HTML and 30–80 KB per mp3, so ~30–40 GB cache total.
3. **Resume-able batches.** Split the wordlist into 1k-word chunks and write one `.apkg` per chunk (or one master deck after all chunks are done). The cache makes re-runs idempotent.
4. **Use `entry_id` for homonyms.** Once the index-page scrape yields all `entry_id`s, parse each by id (`?entry_id=11013058`) instead of by query string — this also fetches every homonym.
5. **Be polite.** ordnet.dk is run by Det Danske Sprog- og Litteraturselskab (a public research body); avoid concurrent floods, set a meaningful User-Agent, and consider emailing them if you intend to redistribute a full deck publicly.

## Development

```bash
uv run -m pytest -q          # parser tests against saved HTML fixtures
uv run python explore.py     # refetch & overwrite samples/*.html
```

## License

Source code: MIT.
Dictionary data: © Det Danske Sprog- og Litteraturselskab — see <https://ordnet.dk/ddo/om/kolofon>. The generated deck is for personal study; don't redistribute it without permission from DSL.
