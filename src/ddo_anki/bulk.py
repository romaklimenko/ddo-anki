"""Async bulk scraper for the full DDO corpus.

Reads a list of entry URLs (from `cache/sitemap/ddo_all_urls.txt`,
populated by `fetch_sitemap.py`) and writes one JSON-encoded entry per
line to `cache/entries.jsonl`. Resumable: any URL whose JSON line is
already in the JSONL is skipped on restart.

Audio mp3 download is a separate phase (`download_audio`), so HTML
scraping and audio fetching can be run/stopped/resumed independently.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter
from pathlib import Path
from typing import Iterable

import httpx
from rich.console import Console

from .scraper import UA, Entry, parse_entry

console = Console()


def _slug(url: str) -> str:
    return url.rsplit("?query=", 1)[-1] if "?query=" in url else url


def read_done_urls(jsonl_path: Path) -> set[str]:
    """Pull the set of already-processed URLs from a JSONL file."""
    if not jsonl_path.exists():
        return set()
    done: set[str] = set()
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = rec.get("url")
            if url:
                done.add(url)
    return done


async def _fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    *,
    max_retries: int = 5,
) -> tuple[str, int, str]:
    """Fetch a URL with retry/backoff. Returns (url, status_code, body_or_error)."""
    backoff = 1.0
    last_status = 0
    last_err = ""
    for attempt in range(max_retries):
        async with sem:
            try:
                r = await client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(backoff + random.random() * 0.5)
                backoff = min(backoff * 2, 30.0)
                continue
        last_status = r.status_code
        if r.status_code == 200:
            return url, 200, r.text
        if r.status_code in (429, 500, 502, 503, 504):
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            await asyncio.sleep(wait + random.random() * 0.5)
            backoff = min(backoff * 2, 60.0)
            continue
        # Other status codes (404, 410, ...): no point retrying.
        return url, r.status_code, ""
    return url, last_status or -1, last_err


async def scrape_bulk(
    urls: list[str],
    jsonl_path: Path,
    *,
    concurrency: int = 5,
    log_every: int = 200,
) -> dict[str, int]:
    """Scrape every URL, parse it, and append one JSON line per entry."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    already = read_done_urls(jsonl_path)
    todo = [u for u in urls if u not in already]
    console.print(
        f"[cyan]Bulk scrape:[/cyan] {len(urls):,} total, "
        f"{len(already):,} already done, [bold]{len(todo):,}[/bold] remaining"
    )
    if not todo:
        return {"total": len(urls), "done": len(already), "added": 0}

    stats: Counter[str] = Counter()
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": UA, "Accept-Language": "da,en;q=0.5"}
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(30.0, connect=10.0)

    started = time.monotonic()
    with jsonl_path.open("a", encoding="utf-8") as out:
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, limits=limits, follow_redirects=True, http2=False
        ) as client:
            tasks = [_fetch_one(client, sem, url) for url in todo]
            processed = 0
            for fut in asyncio.as_completed(tasks):
                url, status, body = await fut
                processed += 1
                if status != 200:
                    stats[f"http_{status}"] += 1
                    record = {"url": url, "status": status, "error": body[:300] if status < 0 else ""}
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                else:
                    try:
                        entry = parse_entry(body, query=_slug(url))
                    except Exception as exc:  # noqa: BLE001
                        stats["parse_error"] += 1
                        out.write(
                            json.dumps(
                                {"url": url, "status": 200, "error": f"parse: {exc!s}"},
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        entry = None
                    if entry is None:
                        stats["no_entry"] += 1
                        out.write(
                            json.dumps({"url": url, "status": 200, "entry": None}, ensure_ascii=False) + "\n"
                        )
                    else:
                        entry.source_url = url
                        record = {"url": url, "status": 200, "entry": entry.to_dict()}
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        stats["ok"] += 1

                if processed % log_every == 0 or processed == len(todo):
                    out.flush()
                    elapsed = time.monotonic() - started
                    rate = processed / elapsed if elapsed > 0 else 0
                    remaining = len(todo) - processed
                    eta = remaining / rate if rate > 0 else 0
                    console.print(
                        f"  {processed:>6}/{len(todo):,}  "
                        f"ok={stats['ok']}  no_entry={stats['no_entry']}  err={stats.total() - stats['ok'] - stats['no_entry']}  "
                        f"{rate:>4.1f} req/s  ETA {eta/60:>4.1f} min"
                    )

    return {"total": len(urls), "done": len(already), "added": processed, **stats}


def iter_jsonl_entries(jsonl_path: Path) -> Iterable[Entry]:
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("entry") is None:
                continue
            yield Entry.from_dict(rec["entry"])


def collect_audio_urls(jsonl_path: Path) -> list[str]:
    """Unique mp3 URLs across every parsed entry."""
    seen: set[str] = set()
    for entry in iter_jsonl_entries(jsonl_path):
        for p in entry.pronunciations:
            if p.audio_url:
                seen.add(p.audio_url)
    return sorted(seen)


def _audio_path(audio_dir: Path, url: str) -> Path:
    """Shard mp3s by first 4 chars of filename, e.g. 1101/11013058_1.mp3."""
    name = url.rsplit("/", 1)[-1]
    shard = name[:4] if len(name) >= 4 else "0000"
    return audio_dir / shard / name


async def _fetch_audio(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    audio_dir: Path,
    *,
    max_retries: int = 4,
) -> str:
    dest = _audio_path(audio_dir, url)
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    backoff = 1.0
    for attempt in range(max_retries):
        async with sem:
            try:
                r = await client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:  # noqa: F841
                await asyncio.sleep(backoff + random.random() * 0.5)
                backoff = min(backoff * 2, 30.0)
                continue
        if r.status_code == 200 and r.content:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(dest)
            return "ok"
        if r.status_code in (429, 500, 502, 503, 504):
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
            await asyncio.sleep(wait + random.random() * 0.5)
            backoff = min(backoff * 2, 60.0)
            continue
        return f"http_{r.status_code}"
    return "max_retries"


async def download_audio(
    jsonl_path: Path,
    audio_dir: Path,
    *,
    concurrency: int = 8,
    log_every: int = 500,
) -> dict[str, int]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    urls = collect_audio_urls(jsonl_path)
    console.print(f"[cyan]Audio:[/cyan] {len(urls):,} unique mp3 URLs")
    if not urls:
        return {}

    stats: Counter[str] = Counter()
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": UA}
    timeout = httpx.Timeout(60.0, connect=10.0)
    started = time.monotonic()

    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
        tasks = [_fetch_audio(client, sem, url, audio_dir) for url in urls]
        processed = 0
        for fut in asyncio.as_completed(tasks):
            outcome = await fut
            stats[outcome] += 1
            processed += 1
            if processed % log_every == 0 or processed == len(urls):
                elapsed = time.monotonic() - started
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (len(urls) - processed) / rate if rate > 0 else 0
                console.print(
                    f"  audio {processed:>6}/{len(urls):,}  "
                    f"ok={stats['ok']} skip={stats['skip']} err={stats.total() - stats['ok'] - stats['skip']}  "
                    f"{rate:>4.1f}/s  ETA {eta/60:>4.1f} min"
                )

    return dict(stats)
