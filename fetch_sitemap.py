"""One-off: download DDO sitemaps and extract every entry URL."""
from __future__ import annotations

import gzip
import re
from pathlib import Path

import httpx

INDEX = "https://ordnet.dk/sitemap_index.xml"
UA = "Mozilla/5.0 (compatible; ddo-anki/0.1)"


def main() -> None:
    cache = Path("cache/sitemap")
    cache.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        idx_xml = c.get(INDEX).text
        ddo_urls = re.findall(r"<loc>([^<]+sitemap_ddo[^<]+)</loc>", idx_xml)
        print(f"Found {len(ddo_urls)} DDO sub-sitemaps: {ddo_urls}")

        all_urls: list[str] = []
        for url in ddo_urls:
            name = url.rsplit("/", 1)[-1]
            local_gz = cache / name
            if not local_gz.exists():
                print(f"  fetching {url}")
                r = c.get(url)
                r.raise_for_status()
                local_gz.write_bytes(r.content)
            xml = gzip.decompress(local_gz.read_bytes()).decode("utf-8")
            urls = re.findall(r"<loc>([^<]+)</loc>", xml)
            print(f"  {name}: {len(urls):,} URLs")
            all_urls.extend(urls)

    out = cache / "ddo_all_urls.txt"
    out.write_text("\n".join(all_urls), encoding="utf-8")
    print(f"\nTotal entry URLs: {len(all_urls):,} -> {out}")
    for u in all_urls[:5]:
        print(f"  e.g. {u}")


if __name__ == "__main__":
    main()
