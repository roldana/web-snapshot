import argparse
import sys
import json
import gzip
import xml.etree.ElementTree as ET
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
    "/wp-sitemap.xml",
    "/sitemap/sitemap.xml",
    "/sitemap/sitemap-index.xml",
    "/sitemap/sitemap_index.xml",
    "/sitemap_index_1.xml",
    "/sitemap.xml?no_cache=1",
    "/sitemap.txt",
]

def normalize_base(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("empty url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        raise ValueError("invalid URL: missing host")
    return urlunparse((p.scheme, p.netloc, "", "", "", ""))

def fetch(url: str, timeout: float = 10.0) -> tuple[int, dict[str, str], bytes]:
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": "web-snapshot-sitemap/1.0"})
        return r.status_code, r.headers, r.content
    except requests.RequestException:
        return 0, {}, b""

def decompress_if_needed(data: bytes) -> bytes:
    if not data:
        return b""
    try:
        if data[:2] == b"\x1f\x8b":
            return gzip.decompress(data)
    except Exception:
        pass
    return data

def parse_root_type(xml_bytes: bytes) -> str | None:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    tag = root.tag.rsplit("}", 1)[-1] if "}" in root.tag else root.tag
    if tag == "sitemapindex":
        return "sitemapindex"
    if tag == "urlset":
        return "urlset"
    return None

def extract_sitemaps_from_index(xml_bytes: bytes) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_bytes)
        ns_strip = lambda t: t.rsplit("}", 1)[-1] if "}" in t else t
        for sm in root.findall(".//"):
            if ns_strip(sm.tag) == "loc":
                if sm.text and sm.text.strip():
                    urls.append(sm.text.strip())
    except Exception:
        pass
    return urls

def get_sitemaps_from_root(root_url: str) -> tuple[str, str, list[str]] | None:
    status, headers, body = fetch(root_url)
    if status != 200 or not body:
        return None
    data = decompress_if_needed(body)
    rtype = parse_root_type(data)
    if rtype == "urlset":
        return root_url, rtype, [root_url]
    if rtype == "sitemapindex":
        children = extract_sitemaps_from_index(data)
        return root_url, rtype, children or []
    return None

def find_robots_sitemap(base: str) -> str | None:
    status, headers, body = fetch(base + "/robots.txt")
    if status != 200 or not body:
        return None
    try:
        for line in body.decode("utf-8", errors="ignore").splitlines():
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm:
                    return sm
    except Exception:
        return None
    return None

def fallback_common_root(base: str) -> str | None:
    for path in COMMON_SITEMAP_PATHS:
        url = base + path
        status, headers, body = fetch(url)
        if status == 200 and body:
            data = decompress_if_needed(body)
            rtype = parse_root_type(data)
            if rtype in ("urlset", "sitemapindex"):
                return url
    return None
 
def main() -> None:
    ap = argparse.ArgumentParser(description="Discover sitemaps from robots.txt or common paths")
    ap.add_argument("base", help="Root domain or URL (e.g., example.com or https://example.com)")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON output")
    args = ap.parse_args()

    try:
        base = normalize_base(args.base)
    except ValueError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        sys.exit(2)

    # 1) robots.txt → root sitemap (first Sitemap: entry)
    found_via = None
    root_sitemap = find_robots_sitemap(base)
    if root_sitemap:
        parsed = get_sitemaps_from_root(root_sitemap)
        if parsed:
            root_url, root_type, sitemaps = parsed
            found_via = "robots"
        else:
            root_sitemap = None

    # 2) Fallback to common paths if robots missing or invalid
    if not root_sitemap:
        alt = fallback_common_root(base)
        if alt:
            parsed = get_sitemaps_from_root(alt)
            if parsed:
                root_url, root_type, sitemaps = parsed
                root_sitemap = root_url
                found_via = "common"

    # Output
    if args.json:
        if root_sitemap:
            print(json.dumps({
                "base": base,
                "found_via": found_via,
                "root_sitemap": root_sitemap,
                "root_type": root_type,
                "sitemaps": sitemaps,
            }, indent=2))
        else:
            print(json.dumps({
                "base": base,
                "found_via": None,
                "root_sitemap": None,
                "root_type": None,
                "sitemaps": [],
            }, indent=2))
        return

    print(f"Base: {base}")
    if root_sitemap:
        print(f"Root sitemap: {root_sitemap} (type={root_type}) [via {found_via}]")
        if root_type == "sitemapindex":
            print("Child sitemaps:")
            for u in sitemaps:
                print(f" - {u}")
        else:
            print("Using single sitemap (urlset)")
    else:
        print("No sitemap found via robots or common paths.")

if __name__ == "__main__":
    main()
