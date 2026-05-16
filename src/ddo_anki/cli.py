"""CLI: scrape DDO and build Anki decks.

Subcommands:
    build         - build a deck from a small wordlist file (POC mode)
    bulk-scrape   - async-scrape every URL in a list, append entries to a JSONL
    bulk-audio    - async-download all mp3s referenced by the JSONL
    bulk-build    - build one big .apkg from a JSONL + audio cache
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from .bulk import (
    collect_audio_urls,
    download_audio,
    iter_jsonl_entries,
    prune_retryable,
    scrape_bulk,
    _audio_path,
)
from .deck import build_deck
from .scraper import DdoScraper, Entry

console = Console()


def read_word_list(path: Path) -> list[str]:
    words: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        w = raw.strip()
        if not w or w.startswith("#"):
            continue
        words.append(w)
    return words


def cmd_build(args: argparse.Namespace) -> int:
    if not args.wordlist.exists():
        console.print(f"[red]wordlist not found: {args.wordlist}[/red]")
        return 2
    words = read_word_list(args.wordlist)
    if not words:
        console.print("[yellow]No words in input.[/yellow]")
        return 1
    console.print(f"Looking up [bold]{len(words)}[/bold] word(s) from [cyan]{args.wordlist}[/cyan]")

    entries: list[Entry] = []
    audio_files: dict[str, str] = {}
    audio_paths: list[Path] = []

    with DdoScraper(cache_dir=args.cache_dir, delay=args.delay) as scraper:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                      BarColumn(), MofNCompleteColumn(), console=console, transient=True) as prog:
            task = prog.add_task("Fetching entries", total=len(words))
            for w in words:
                prog.update(task, description=f"[cyan]{w}")
                try:
                    e = scraper.lookup(w)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]{w}: {exc}[/red]")
                    prog.advance(task)
                    continue
                if e is None:
                    console.print(f"[yellow]{w}: no entry found[/yellow]")
                    prog.advance(task)
                    continue
                if not args.no_audio:
                    for pron in e.pronunciations:
                        if not pron.audio_url or pron.audio_url in audio_files:
                            continue
                        path = scraper.fetch_audio(pron.audio_url, args.audio_dir)
                        if path:
                            audio_files[pron.audio_url] = path.name
                            audio_paths.append(path)
                entries.append(e)
                prog.advance(task)

    if not entries:
        console.print("[red]No entries scraped - nothing to build.[/red]")
        return 1

    console.print(f"Building deck with [bold]{len(entries)}[/bold] cards and [bold]{len(audio_paths)}[/bold] audio file(s)")
    result = build_deck(entries, args.out, deck_name=args.deck_name,
                       audio_files=audio_files, audio_paths=audio_paths)
    console.print(f"[green]Wrote {result.apkg} ({result.cards} cards, {result.audio_files} mp3s).[/green]")
    return 0


def cmd_bulk_scrape(args: argparse.Namespace) -> int:
    if not args.urls_file.exists():
        console.print(f"[red]URL list not found: {args.urls_file}[/red]")
        return 2
    urls = [u.strip() for u in args.urls_file.read_text(encoding="utf-8").splitlines() if u.strip()]
    if args.limit:
        urls = urls[: args.limit]
    stats = asyncio.run(
        scrape_bulk(
            urls,
            args.jsonl,
            concurrency=args.concurrency,
            log_every=args.log_every,
            max_retries=args.max_retries,
        )
    )
    console.print(f"[green]Done.[/green] stats={stats}")
    return 0


def cmd_bulk_retry(args: argparse.Namespace) -> int:
    """Prune retryable failures from the JSONL, then re-scrape just those."""
    if not args.urls_file.exists():
        console.print(f"[red]URL list not found: {args.urls_file}[/red]")
        return 2
    if not args.jsonl.exists():
        console.print(f"[red]JSONL not found: {args.jsonl}[/red]")
        return 2

    console.print(f"Pruning retryable failures from [cyan]{args.jsonl}[/cyan] ...")
    prune_stats = prune_retryable(args.jsonl)
    console.print(
        f"  kept={prune_stats['kept']:,}  "
        f"dropped={prune_stats['dropped']:,}  "
        f"total_lines_seen={prune_stats['total_lines']:,}"
    )
    if prune_stats["dropped"] == 0:
        console.print("[green]Nothing to retry.[/green]")
        return 0

    urls = [u.strip() for u in args.urls_file.read_text(encoding="utf-8").splitlines() if u.strip()]
    stats = asyncio.run(
        scrape_bulk(
            urls,
            args.jsonl,
            concurrency=args.concurrency,
            log_every=args.log_every,
            max_retries=args.max_retries,
        )
    )
    console.print(f"[green]Retry done.[/green] stats={stats}")
    return 0


def cmd_bulk_audio(args: argparse.Namespace) -> int:
    if not args.jsonl.exists():
        console.print(f"[red]JSONL not found: {args.jsonl}[/red]")
        return 2
    stats = asyncio.run(
        download_audio(args.jsonl, args.audio_dir, concurrency=args.concurrency, log_every=args.log_every)
    )
    console.print(f"[green]Done.[/green] stats={stats}")
    return 0


def cmd_bulk_build(args: argparse.Namespace) -> int:
    if not args.jsonl.exists():
        console.print(f"[red]JSONL not found: {args.jsonl}[/red]")
        return 2

    entries: list[Entry] = []
    audio_files: dict[str, str] = {}
    audio_paths: list[Path] = []
    missing_audio = 0

    console.print("Loading entries from JSONL...")
    for entry in iter_jsonl_entries(args.jsonl):
        for p in entry.pronunciations:
            if not p.audio_url or p.audio_url in audio_files:
                continue
            local = _audio_path(args.audio_dir, p.audio_url)
            if local.exists() and local.stat().st_size > 0:
                audio_files[p.audio_url] = local.name
                audio_paths.append(local)
            else:
                missing_audio += 1
        entries.append(entry)

    console.print(
        f"Loaded {len(entries):,} entries, {len(audio_paths):,} audio files "
        f"({missing_audio:,} mp3 URLs missing locally)"
    )
    if not entries:
        return 1

    console.print(f"Writing .apkg to [cyan]{args.out}[/cyan] ...")
    result = build_deck(entries, args.out, deck_name=args.deck_name,
                       audio_files=audio_files, audio_paths=audio_paths)
    console.print(f"[green]Wrote {result.apkg} ({result.cards:,} cards, {result.audio_files:,} mp3s).[/green]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ddo-anki", description="Build Anki decks from Den Danske Ordbog")
    sub = p.add_subparsers(dest="cmd", required=False)

    # Default subcommand (legacy positional): `build`
    pb = sub.add_parser("build", help="Build a deck from a wordlist (POC).")
    pb.add_argument("wordlist", type=Path, help="One Danish word per line")
    pb.add_argument("-o", "--out", type=Path, default=Path("out/ddo.apkg"))
    pb.add_argument("--deck-name", default="Den Danske Ordbog")
    pb.add_argument("--cache-dir", type=Path, default=Path("cache/html"))
    pb.add_argument("--audio-dir", type=Path, default=Path("cache/audio"))
    pb.add_argument("--no-audio", action="store_true")
    pb.add_argument("--delay", type=float, default=0.6)
    pb.set_defaults(func=cmd_build)

    ps = sub.add_parser("bulk-scrape", help="Async-scrape all URLs in a list into a JSONL.")
    ps.add_argument("urls_file", type=Path, help="Text file with one DDO URL per line")
    ps.add_argument("--jsonl", type=Path, default=Path("cache/entries.jsonl"))
    ps.add_argument("--concurrency", type=int, default=5)
    ps.add_argument("--log-every", type=int, default=200)
    ps.add_argument("--limit", type=int, default=0, help="Process only the first N URLs (0 = all)")
    ps.add_argument("--max-retries", type=int, default=5, help="Per-URL retry budget on 429/5xx")
    ps.set_defaults(func=cmd_bulk_scrape)

    pr = sub.add_parser(
        "bulk-retry",
        help="Re-scrape every URL whose JSONL record is a retryable failure (429/5xx/network).",
    )
    pr.add_argument("urls_file", type=Path, help="Same URL list used for the original scrape")
    pr.add_argument("--jsonl", type=Path, default=Path("cache/entries.jsonl"))
    pr.add_argument("--concurrency", type=int, default=3, help="Lower default - retries hit a busy server")
    pr.add_argument("--log-every", type=int, default=200)
    pr.add_argument("--max-retries", type=int, default=8, help="Higher default to ride out 503 windows")
    pr.set_defaults(func=cmd_bulk_retry)

    pa = sub.add_parser("bulk-audio", help="Async-download all mp3s referenced by a JSONL.")
    pa.add_argument("--jsonl", type=Path, default=Path("cache/entries.jsonl"))
    pa.add_argument("--audio-dir", type=Path, default=Path("cache/audio"))
    pa.add_argument("--concurrency", type=int, default=8)
    pa.add_argument("--log-every", type=int, default=500)
    pa.set_defaults(func=cmd_bulk_audio)

    bb = sub.add_parser("bulk-build", help="Build one big .apkg from a JSONL + audio cache.")
    bb.add_argument("--jsonl", type=Path, default=Path("cache/entries.jsonl"))
    bb.add_argument("--audio-dir", type=Path, default=Path("cache/audio"))
    bb.add_argument("-o", "--out", type=Path, default=Path("out/ddo-full.apkg"))
    bb.add_argument("--deck-name", default="Den Danske Ordbog")
    bb.set_defaults(func=cmd_bulk_build)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Legacy: `ddo-anki wordlist.txt -o out.apkg` (no subcommand)
    if getattr(args, "cmd", None) is None:
        # try to fall through to `build` for backwards compatibility
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
