import argparse
import sys
import re
import json
import gzip
from io import BytesIO
from urllib.parse import urlparse, urlunparse

import requests


COMMON_SITEMAP_PATHS = [
    "/sitemap.xml",
    "/sitemap.xml.gz",
    "/sitemap_index.xml",
    "/sitemap_index.xml.gz",
    "/sitemap-index.xml",
    "/sitemap-index.xml.gz",
    "/sitemapindex.xml",
    "/sitemaps.xml",
    "/sitemap1.xml",
    "/sitemap-1.xml",
    "/sitemap_1.xml",
    "/sitemap_1.xml.gz",
    "/sitemap-news.xml",
    "/news-sitemap.xml",
    "/post-sitemap.xml",
    "/page-sitemap.xml",
    "/product-sitemap.xml",
    "/category-sitemap.xml",
    "/tag-sitemap.xml",
    "/wp-sitemap.xml",  # WordPress 5.5+
    "/sitemap/sitemap.xml",
    "/sitemap/sitemap-index.xml",
    "/sitemap/sitemap_index.xml",
    "/sitemap_index_1.xml",
    "/sitemap.txt",  # some sites publish plain text list
]

ADDITIONAL_SITEMAP_PATHS = [
    "/sitemap.xml?no_cache=1"  # some CDNs
]


XML_ROOT_RE = re.compile(rb"<\?xml[^>]*>\s*<(?P<root>tag|urlset|sitemapindex)[\s>]")
URLSET_RE = re.compile(rb"<urlset[\s>]")
SITEMAPINDEX_RE = re.compile(rb"<sitemapindex[\s>]")


def normalize_base(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("empty url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        raise ValueError("invalid URL: missing host")
    # strip path/query/fragment; keep scheme+netloc
    return urlunparse((p.scheme, p.netloc, "", "", "", ""))


def is_sitemap_bytes(data: bytes, content_type: str | None) -> bool:
    if not data:
        return False
    # try to detect gzip raw content regardless of headers
    try:
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
    except Exception:
        pass

    # quick content-type hint
    if content_type and ("xml" in content_type or "text/plain" in content_type or "application/octet-stream" in content_type):
        pass

    head = data[:2048].strip()
    if URLSET_RE.search(head) or SITEMAPINDEX_RE.search(head):
        return True
    # looser XML root check
    if b"<urlset" in head or b"<sitemapindex" in head:
        return True
    # allow simple text list of URLs (sitemap.txt)
    try:
        text = head.decode("utf-8", errors="ignore")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and all(ln.startswith("http") for ln in lines[:5]):
            return True
    except Exception:
        pass
    return False


def fetch(url: str, timeout: float = 10.0) -> tuple[int, dict[str, str], bytes]:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": "web-snapshot-sitemap/1.0"})
        return r.status_code, r.headers, r.content
    except requests.RequestException:
        return 0, {}, b""


def probe_candidates(base: str) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()

    # robots.txt discovery
    robots_url = base + "/robots.txt"
    status, headers, body = fetch(robots_url)
    if status == 200 and body:
        try:
            text = body.decode("utf-8", errors="ignore")
            for line in text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidate = line.split(":", 1)[1].strip()
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        c_status, c_headers, c_body = fetch(candidate)
                        is_map = c_status == 200 and is_sitemap_bytes(c_body, c_headers.get("content-type"))
                        results.append({
                            "source": "robots",
                            "url": candidate,
                            "status": c_status,
                            "is_sitemap": is_map,
                            "content_type": c_headers.get("content-type"),
                        })
        except Exception:
            pass

    # common paths under base
    for path in COMMON_SITEMAP_PATHS:
        url = base + path
        if url in seen:
            continue
        seen.add(url)
        status, headers, body = fetch(url)
        ok = status == 200 and is_sitemap_bytes(body, headers.get("content-type"))
        results.append({
            "source": "common",
            "url": url,
            "status": status,
            "is_sitemap": ok,
            "content_type": headers.get("content-type"),
        })

    return results


def main():
    ap = argparse.ArgumentParser(description="Probe common sitemap URLs and robots.txt for sitemap discovery")
    ap.add_argument("base", help="Root domain or URL (e.g., example.com or https://example.com)")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON output")
    args = ap.parse_args()

    try:
        base = normalize_base(args.base)
    except ValueError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        sys.exit(2)

    results = probe_candidates(base)

    if args.json:
        print(json.dumps({"base": base, "results": results}, indent=2))
        return

    print(f"Base: {base}")
    found = [r for r in results if r.get("is_sitemap")]
    if found:
        print("Discovered sitemaps:")
        for r in found:
            ct = r.get("content_type") or ""
            print(f" - {r['url']}  (source={r['source']}, type={ct})")
    else:
        print("No sitemaps discovered at common locations.")

    print("\nChecked:")
    for r in results:
        mark = "OK" if r.get("is_sitemap") else str(r.get("status"))
        print(f" - {r['url']}: {mark}")


if __name__ == "__main__":
    main()
