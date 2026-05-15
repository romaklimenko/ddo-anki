"""One-off exploration script - fetch a DDO entry and dump structure."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

WORDS = ["finit", "hus", "være", "smuk", "hund"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def main() -> None:
    out = Path("samples")
    out.mkdir(exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        for w in WORDS:
            r = c.get("https://ordnet.dk/ddo/ordbog", params={"query": w})
            r.raise_for_status()
            (out / f"{w}.html").write_text(r.text, encoding="utf-8")
            print(f"{w}: {len(r.text):,} bytes -> samples/{w}.html")


if __name__ == "__main__":
    sys.exit(main() or 0)
