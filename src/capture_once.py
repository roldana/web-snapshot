import os
import json
import argparse
import importlib.util

HERE = os.path.dirname(__file__)
MODULE_PATH = os.path.join(HERE, "web_capture.py")
spec = importlib.util.spec_from_file_location("web_capture", MODULE_PATH)
web_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_capture)

parser = argparse.ArgumentParser(description="Run one-off capture for one or more URLs")
parser.add_argument("urls", nargs="+", help="URLs to capture")
args = parser.parse_args()

urls = [url if url.startswith(('http://', 'https://')) else f'https://{url}' for url in args.urls]
print(f"Running capture for URLs: {urls}")

result = web_capture.capture_urls(urls)
print(json.dumps(result, indent=2))