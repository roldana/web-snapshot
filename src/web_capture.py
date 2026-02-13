import os
import hashlib
import urllib.parse
import logging
from playwright.sync_api import sync_playwright
import json
import re
import datetime

VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
FULL_SCREENSHOT = True
CAPTURE_HEIGHT = 6000
SNAPSHOT_DIR = "data/snapshots"
SCREENSHOT_DIR = "screenshots"
HTML_DIR = "html"
LOGS_DIR = "data/logs"

os.makedirs(LOGS_DIR, exist_ok=True)

logging.basicConfig(
    filename=f"{LOGS_DIR}/scrape.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logging.info("Starting scraping process")

def load_urls(file_path):
    with open(file_path, 'r') as f:
        data = json.load(f)
    return data.get("scrape_urls", [])

def hash_file(filepath: str) -> str:
    # Return MD5 hash of the file
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def get_latest_screenshot_hash(domain_dir: str, curr_file: str) -> str:
    # If the directory doesn't exist, there is no previous screenshot
    if not os.path.isdir(domain_dir):
        return None

    # List all .png files in the directory except the current one
    files = [
        f for f in os.listdir(domain_dir)
        if f.endswith(".png") and f != os.path.basename(curr_file)
    ]
    if not files:
        return None

    # Sort files by modification time (ascending)
    files.sort(key=lambda f: os.path.getmtime(os.path.join(domain_dir, f)))
    latest_file = os.path.join(domain_dir, files[-1])
    return hash_file(latest_file)

def _short_hash(text: str, length: int = 8) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:length]


def _slugify_path(url: str) -> str:
    """Map a URL to a stable slug filename per domain.

    Rules (simple, readable, unique per URL):
    - Homepage ("/" or empty path) => "index"
    - Plain path like "/about" => "about"
    - Nested path like "/a/b" => "a-b"
    - If a query string exists, append a short hash suffix to disambiguate: "about-<hash>"
    - Only use [a-z0-9-]; collapse multiple dashes
    """
    p = urllib.parse.urlparse(url)
    path = (p.path or "/").strip()
    if path in ("", "/"):
        base = "index"
    else:
        segs = [s for s in path.split("/") if s]
        base = "-".join(segs)
    # slugify
    base = base.lower()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = re.sub(r"-+", "-", base).strip("-") or "index"
    if p.query:
        base = f"{base}-{_short_hash(url)}"
    return base

def capture_urls(url_list):
    """Capture screenshots and HTML for each URL in url_list.
    Previously in main().
    Callable from a web backend.
    """
    results = []

    # Group URLs by domain so we can reuse a single browser instance per domain
    domain_map = {}
    for url in url_list:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        folder_name = domain  # keep dots in domain for folder clarity
        domain_map.setdefault(folder_name, []).append(url)

    # For each domain, launch a separate browser instance and reuse it for all URLs
    for folder_name, urls in domain_map.items():
        screenshot_folder = os.path.join(SNAPSHOT_DIR, folder_name, SCREENSHOT_DIR)
        html_folder = os.path.join(SNAPSHOT_DIR, folder_name, HTML_DIR)
        os.makedirs(screenshot_folder, exist_ok=True)
        os.makedirs(html_folder, exist_ok=True)

        try:
            with sync_playwright() as p:
                logging.info(f"Starting browser for domain: {folder_name}")
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})

                for url in urls:
                    print(f"\n[INFO] Handling URL: {url}")
                    try:
                        page = context.new_page()
                        page.goto(url)
                        page.wait_for_load_state("networkidle", timeout=60000)

                        page.wait_for_timeout(1000)
                        # Scroll to the bottom of the page to trigger lazy load
                        page.evaluate(
                            """() => {
                                return new Promise(resolve => {
                                    let totalHeight = 0;
                                    const distance = 100;
                                    const timer = setInterval(() => {
                                        window.scrollBy(0, distance);
                                        totalHeight += distance;
                                        if (totalHeight >= document.body.scrollHeight) {
                                            clearInterval(timer);
                                            resolve();
                                        }
                                    }, 100);
                                });
                            }"""
                        )
                        page.wait_for_timeout(2000)

                        # Scroll back to the top so that floating navbars appear correctly
                        page.evaluate("window.scrollTo(0, 0)")
                        page.wait_for_timeout(1000)

                        # Map URL to stable filename within the domain
                        slug = _slugify_path(url)

                        # save HTML content of the page (timestamped local time)
                        html_content = page.content()
                        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        html_filename = f"{slug}_{ts}.html"
                        html_path = os.path.join(html_folder, html_filename)
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        print(f"[INFO] HTML saved: {os.path.join(folder_name, HTML_DIR, html_filename)}")

                        # Save screenshot (full page by default) using the same slug and timestamp
                        screenshot_filename = f"{slug}_{ts}.png"
                        screenshot_path = os.path.join(screenshot_folder, screenshot_filename)
                        page.screenshot(path=screenshot_path, full_page=FULL_SCREENSHOT)

                        page.close()

                        results.append({
                            "url": url,
                            "status": "saved",
                            "html_path": html_path,
                            "screenshot_path": screenshot_path,
                        })

                        logging.info(f"Finished scraping URL: {url} for domain: {folder_name}")

                    except Exception as e:
                        logging.error(f"Error scraping {url}: {e}", exc_info=True)
                        results.append({"url": url, "status": "error", "error": str(e)})
                        # continue with next URL on same domain
                        continue

                browser.close()
        except Exception as e:
            logging.error(f"Error launching browser for domain {folder_name}: {e}", exc_info=True)
            for url in urls:
                results.append({"url": url, "status": "error", "error": str(e)})
            continue

    return results

def main():
    URLS = load_urls("data/urls.json")
    capture_urls(URLS)

if __name__ == "__main__":
    main()