"""CLI: read a word list, scrape DDO, build an Anki deck."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ddo-anki", description="Build an Anki deck from Den Danske Ordbog")
    p.add_argument("wordlist", type=Path, help="Text file with one Danish word per line")
    p.add_argument("-o", "--out", type=Path, default=Path("out/ddo.apkg"), help="Output .apkg path")
    p.add_argument("--deck-name", default="Den Danske Ordbog", help="Anki deck name shown in the app")
    p.add_argument("--cache-dir", type=Path, default=Path("cache/html"), help="Where to cache fetched HTML")
    p.add_argument("--audio-dir", type=Path, default=Path("cache/audio"), help="Where to cache downloaded mp3 files")
    p.add_argument("--no-audio", action="store_true", help="Skip downloading mp3 audio files")
    p.add_argument("--delay", type=float, default=0.6, help="Min seconds between HTTP requests")
    args = p.parse_args(argv)

    if not args.wordlist.exists():
        console.print(f"[red]wordlist not found: {args.wordlist}[/red]")
        return 2

    words = read_word_list(args.wordlist)
    if not words:
        console.print("[yellow]No words in input.[/yellow]")
        return 1
    console.print(f"Looking up [bold]{len(words)}[/bold] word(s) from [cyan]{args.wordlist}[/cyan]")

    entries: list[Entry] = []
    audio_files: dict[str, str] = {}   # url -> filename
    audio_paths: list[Path] = []

    with DdoScraper(cache_dir=args.cache_dir, delay=args.delay) as scraper:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            transient=True,
        ) as prog:
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
    result = build_deck(
        entries,
        args.out,
        deck_name=args.deck_name,
        audio_files=audio_files,
        audio_paths=audio_paths,
    )
    console.print(f"[green]Wrote {result.apkg} ({result.cards} cards, {result.audio_files} mp3s).[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
