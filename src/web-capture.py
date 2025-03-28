import os
import hashlib
import datetime
import urllib.parse
import logging
from playwright.sync_api import sync_playwright
import json

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
    return data.get("sample_urls", [])

def hash_file(filepath: str) -> str:
    # Return MD5 hash of the file
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def get_latest_screenshot_hash(domain_dir: str, curr_file: str) -> str:
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

def main():
    URLS = load_urls("data/urls.json")
    for url in URLS:
        print(f"\n[INFO] Handling URL: {url}")

        # Extract and format the domain name
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        folder_name = domain.replace(".", "_")

        # os.makedirs("{folder_name}\screenshots", exist_ok=True)
        # os.makedirs(HTML_DIR, exist_ok=True)

        # create screenshot and html directories for the domain if they don't exist
        screenshot_folder = os.path.join(SNAPSHOT_DIR, folder_name, SCREENSHOT_DIR)
        html_folder = os.path.join(SNAPSHOT_DIR, folder_name, HTML_DIR)

        try:
            with sync_playwright() as p:
                logging.info(f"Scraping URL: {url} for domain: {folder_name}")
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
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
                # Optionally wait a moment after scrolling for any pending network requests
                page.wait_for_timeout(2000)
                # Scroll back to the top so that floating navbars appear correctly
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)

                viewport_size = page.viewport_size
                if not viewport_size:
                    print("[WARN] Could not get viewport size. Skipping.")
                    browser.close()
                    continue

                clip_region = {
                    "x": 0,
                    "y": 0,
                    "width": viewport_size["width"],
                    "height": CAPTURE_HEIGHT
                }

                #save HTML content of the page
                html_content = page.content()
                html_filename = f"{folder_name}.html"
                html_path = os.path.join(html_folder, html_filename)
                os.makedirs(os.path.dirname(html_path), exist_ok=True)
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                print(f"[INFO] HTML content saved to: {html_filename}")
                
                # Create unique filename with domain and current date & time
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_filename = f"{folder_name}_{timestamp}.png"
                screenshot_path = os.path.join(screenshot_folder, screenshot_filename)
                if FULL_SCREENSHOT:
                    page.screenshot(path=screenshot_path, full_page=True)
                else:
                    page.screenshot(path=screenshot_path, clip=clip_region)
                browser.close()
        except Exception as e:
            logging.error(f"Error scraping {url}: {e}", exc_info=True)
        

        # Compare with last screenshot for this domain
        previous_hash = get_latest_screenshot_hash(screenshot_folder, screenshot_path)
        current_hash = hash_file(screenshot_path)
        if previous_hash is None:
            print("[INFO] First screenshot for this domain, keeping it.")
        else:
            if current_hash == previous_hash:
                os.remove(screenshot_path)
                print("[INFO] Screenshot is identical to the last one for this domain. Deleting the current screenshot.")
            else:
                print("[INFO] Screenshot is different, keeping it.")

        logging.info(f"Finished scraping URL: {url} for domain: {folder_name}")


if __name__ == "__main__":
    main()