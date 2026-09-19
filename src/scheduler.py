import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests

try:
    import sitemap_scan
except ImportError:
    from . import sitemap_scan


HERE = os.path.dirname(__file__)
DEFAULT_DB_PATH = os.getenv("DB_PATH", os.path.join(HERE, "..", "data", "app.db"))
DEFAULT_INTERVAL_HOURS = 24.0
DEFAULT_POLL_SECONDS = 60.0
DEFAULT_MAX_PAGES = 100


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def default_api_base() -> str:
    configured = os.getenv("WEB_SNAPSHOT_API") or os.getenv("WEB_CAPTURE_API_BASE")
    if configured:
        return configured.rstrip("/")
    return "http://api:5000" if os.path.exists("/.dockerenv") else "http://localhost:5000"


def normalize_domain(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise ValueError("domain must contain a valid host")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def canonical_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_page_url(base: str, href: str) -> str | None:
    url, _fragment = urldefrag(urljoin(base + "/", href))
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or canonical_host(url) != canonical_host(base):
        return None
    return url


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    db_dir = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(db_dir, exist_ok=True)
    with db_connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitored_domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE,
                interval_hours REAL NOT NULL DEFAULT 24,
                include_pages INTEGER NOT NULL DEFAULT 0,
                scan_sitemap INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT,
                last_job_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def add_monitor(db_path: str, domain: str, interval_hours: float, include_pages: bool, scan_sitemap: bool) -> None:
    if interval_hours <= 0:
        raise ValueError("interval hours must be greater than zero")
    domain = normalize_domain(domain)
    include_pages = include_pages or scan_sitemap
    timestamp = now_utc_iso()
    with db_connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO monitored_domains (
                domain, interval_hours, include_pages, scan_sitemap,
                enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                interval_hours = excluded.interval_hours,
                include_pages = excluded.include_pages,
                scan_sitemap = excluded.scan_sitemap,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (domain, interval_hours, int(include_pages), int(scan_sitemap), timestamp, timestamp),
        )
        conn.commit()
    print(f"Monitoring {domain} every {interval_hours:g} hours.")


def remove_monitor(db_path: str, domain: str) -> None:
    domain = normalize_domain(domain)
    with db_connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM monitored_domains WHERE domain = ?", (domain,))
        conn.commit()
    if cursor.rowcount:
        print(f"Removed {domain}.")
    else:
        print(f"No monitor found for {domain}.")


def list_monitors(db_path: str) -> None:
    with db_connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT domain, interval_hours, include_pages, scan_sitemap,
                   enabled, last_run_at, last_job_id, last_error
            FROM monitored_domains
            ORDER BY domain
            """
        ).fetchall()
    print(json.dumps([dict(row) for row in rows], indent=2))


def discover_homepage_links(base: str) -> list[str]:
    response = requests.get(
        base + "/",
        timeout=20,
        headers={"User-Agent": "web-snapshot-scheduler/1.0"},
    )
    response.raise_for_status()
    parser = LinkParser()
    parser.feed(response.text)
    return [url for href in parser.links if (url := normalize_page_url(base, href))]


def discover_sitemap_links(base: str, max_pages: int) -> list[str]:
    root_sitemap = sitemap_scan.find_robots_sitemap(base) or sitemap_scan.fallback_common_root(base)
    if not root_sitemap:
        return []

    pages = []
    sitemap_queue = [urljoin(base + "/", root_sitemap)]
    visited_sitemaps = set()

    while sitemap_queue and len(pages) < max_pages:
        sitemap_url = sitemap_queue.pop(0)
        if sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)

        status, _headers, body = sitemap_scan.fetch(sitemap_url, timeout=20)
        if status != 200 or not body:
            continue
        data = sitemap_scan.decompress_if_needed(body)
        sitemap_type = sitemap_scan.parse_root_type(data)
        links = sitemap_scan.extract_sitemaps_from_index(data)

        if sitemap_type == "sitemapindex":
            sitemap_queue.extend(urljoin(sitemap_url, link) for link in links)
        elif sitemap_type == "urlset":
            pages.extend(url for href in links if (url := normalize_page_url(base, href)))

    return pages[:max_pages]


def build_capture_urls(monitor: sqlite3.Row, max_pages: int) -> list[str]:
    base = monitor["domain"]
    urls = [base + "/"]
    if not monitor["include_pages"]:
        return urls

    try:
        urls.extend(discover_homepage_links(base))
    except requests.RequestException as error:
        print(f"[{base}] Could not scan homepage links: {error}", file=sys.stderr)

    if monitor["scan_sitemap"]:
        urls.extend(discover_sitemap_links(base, max_pages))

    return list(dict.fromkeys(urls))[:max_pages]


def is_due(monitor: sqlite3.Row, current_time: datetime) -> bool:
    if not monitor["last_run_at"]:
        return True
    try:
        last_run = datetime.fromisoformat(monitor["last_run_at"])
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return current_time >= last_run + timedelta(hours=monitor["interval_hours"])


def save_run_result(db_path: str, monitor_id: int, job_id: str | None = None, error: str | None = None) -> None:
    timestamp = now_utc_iso()
    with db_connect(db_path) as conn:
        if error is None:
            conn.execute(
                """
                UPDATE monitored_domains
                SET last_run_at = ?, last_job_id = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, job_id, timestamp, monitor_id),
            )
        else:
            conn.execute(
                "UPDATE monitored_domains SET last_error = ?, updated_at = ? WHERE id = ?",
                (error, timestamp, monitor_id),
            )
        conn.commit()


def run_monitor(db_path: str, api_base: str, monitor: sqlite3.Row, max_pages: int) -> None:
    domain = monitor["domain"]
    try:
        urls = build_capture_urls(monitor, max_pages)
        response = requests.post(
            f"{api_base.rstrip('/')}/capture",
            json={"urls": urls},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok") or not payload.get("job_id"):
            raise RuntimeError(f"unexpected API response: {payload}")
        job_id = payload["job_id"]
        save_run_result(db_path, monitor["id"], job_id=job_id)
        print(f"[{domain}] Queued job {job_id} for {len(urls)} URL(s).")
    except Exception as error:
        save_run_result(db_path, monitor["id"], error=str(error))
        print(f"[{domain}] Scheduler error: {error}", file=sys.stderr)


def run_due_monitors(db_path: str, api_base: str, max_pages: int) -> None:
    with db_connect(db_path) as conn:
        monitors = conn.execute(
            "SELECT * FROM monitored_domains WHERE enabled = 1 ORDER BY id"
        ).fetchall()
    current_time = now_utc()
    for monitor in monitors:
        if is_due(monitor, current_time):
            run_monitor(db_path, api_base, monitor, max_pages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Schedule recurring web snapshots through web_api.py")
    parser.add_argument("command", nargs="?", choices=("run", "init", "add", "list", "remove"), default="run")
    parser.add_argument("domain", nargs="?", help="Domain for the add or remove command")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--api", default=default_api_base(), help="Web Snapshot API base URL")
    parser.add_argument("--interval-hours", type=float, default=DEFAULT_INTERVAL_HOURS)
    parser.add_argument("--include-pages", action="store_true", help="Also capture same-domain pages linked by the homepage")
    parser.add_argument("--scan-sitemap", action="store_true", help="Also discover pages from the site's sitemap")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Maximum URLs per capture job")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="Run due monitors once and exit")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    init_db(args.db)

    try:
        if args.command == "init":
            print(f"Scheduler database initialized at {os.path.abspath(args.db)}.")
        elif args.command == "add":
            if not args.domain:
                raise ValueError("the add command requires a domain")
            add_monitor(args.db, args.domain, args.interval_hours, args.include_pages, args.scan_sitemap)
        elif args.command == "remove":
            if not args.domain:
                raise ValueError("the remove command requires a domain")
            remove_monitor(args.db, args.domain)
        elif args.command == "list":
            list_monitors(args.db)
        else:
            if args.max_pages < 1 or args.poll_seconds <= 0:
                raise ValueError("max pages and poll seconds must be greater than zero")
            while True:
                run_due_monitors(args.db, args.api, args.max_pages)
                if args.once:
                    break
                time.sleep(args.poll_seconds)
    except (ValueError, sqlite3.Error) as error:
        print(f"Scheduler error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == "__main__":
    main()
