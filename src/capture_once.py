import os
import sys
import json
import time
import argparse
from urllib.parse import urlparse

import requests


def default_api_base():
	# Priority: explicit env overrides
	for key in ("WEB_SNAPSHOT_API", "WEB_CAPTURE_API_BASE"):
		val = os.environ.get(key)
		if val:
			return val.rstrip("/")
	# Heuristic: inside Docker/Compose prefer service DNS name
	in_container = os.path.exists("/.dockerenv") or "container" in open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore").read()
	return ("http://api:5000" if in_container else "http://localhost:5000")


def normalize_urls(raw_urls):
	cleaned = []
	for u in raw_urls:
		if not u:
			continue
		if not u.startswith(("http://", "https://")):
			u = f"https://{u}"
		# basic sanity: has netloc when parsed
		parsed = urlparse(u)
		if not parsed.netloc:
			continue
		cleaned.append(u)
	return cleaned


def main():
	parser = argparse.ArgumentParser(description="Run one-off capture via API so URLs are stored in DB")
	parser.add_argument("urls", nargs="+", help="URLs to capture")
	parser.add_argument(
		"--api",
		dest="api_base",
		default=default_api_base(),
		help="API base URL (auto: host localhost; Docker uses http://api:5000)",
	)
	parser.add_argument(
		"--poll",
		dest="poll_interval",
		type=float,
		default=1.0,
		help="Polling interval seconds for job status (default: 1.0)",
	)
	parser.add_argument(
		"--timeout",
		dest="timeout",
		type=float,
		default=0.0,
		help="Optional overall timeout in seconds (0 = no timeout)",
	)
	args = parser.parse_args()

	urls = normalize_urls(args.urls)
	if not urls:
		print("No valid URLs to capture.", file=sys.stderr)
		sys.exit(2)

	api_base = args.api_base.rstrip("/")

	try:
		# Kick off job
		resp = requests.post(f"{api_base}/capture", json={"urls": urls}, timeout=60)
	except requests.RequestException as e:
		print(f"Failed to reach API at {api_base}: {e}", file=sys.stderr)
		print("Hint: start it with 'python src/web_api.py' or docker-compose.", file=sys.stderr)
		sys.exit(2)

	if resp.status_code not in (200, 202):
		print(f"API error: HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
		sys.exit(1)

	payload = resp.json()
	if not payload.get("ok"):
		print(f"API responded with error: {payload}", file=sys.stderr)
		sys.exit(1)

	job_id = payload.get("job_id")
	if not job_id:
		print(f"Unexpected API response (missing job_id): {payload}", file=sys.stderr)
		sys.exit(1)

	# Poll for completion
	start_ts = time.time()
	last_status = None
	while True:
		try:
			s = requests.get(f"{api_base}/status/{job_id}", timeout=30)
		except requests.RequestException as e:
			print(f"Error polling job status: {e}", file=sys.stderr)
			time.sleep(args.poll_interval)
			continue

		if s.status_code != 200:
			print(f"Status check failed: HTTP {s.status_code}: {s.text}", file=sys.stderr)
			time.sleep(args.poll_interval)
			continue

		body = s.json()
		if not body.get("ok"):
			print(f"Status error: {body}", file=sys.stderr)
			time.sleep(args.poll_interval)
			continue

		status = body.get("status")
		if status != last_status:
			print(f"Job {job_id} status: {status}")
			last_status = status

		if status == "done":
			result = body.get("result", [])
			print(json.dumps(result, indent=2))
			break
		if status == "error":
			err = body.get("error", "unknown error")
			print(f"Job failed: {err}", file=sys.stderr)
			sys.exit(1)

		if args.timeout and (time.time() - start_ts) > args.timeout:
			print("Timed out waiting for job completion.", file=sys.stderr)
			sys.exit(3)

		time.sleep(args.poll_interval)


if __name__ == "__main__":
	main()