from flask import Flask, render_template, request, redirect, url_for, flash
import os
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

APP = Flask(__name__)
APP.secret_key = os.getenv("FRONTEND_SECRET_KEY", "dev-secret-change-me")

HERE = os.path.dirname(__file__)

# Point this to your existing capture API service
# Example (local): http://localhost:5000
# Example (docker compose service name): http://api:5000
API_BASE = os.getenv("WEB_CAPTURE_API_BASE", "http://localhost:5000")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_url(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return None
    return raw


@APP.route("/", methods=["GET"])
def index():
    # Fetch latest jobs from API as the single source of truth
    history = []
    try:
        resp = requests.get(f"{API_BASE}/jobs", params={"limit": 50}, timeout=10)
        if resp.ok and resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            if data.get("ok") and isinstance(data.get("jobs"), list):
                history = data["jobs"]
    except Exception:
        # Keep page load resilient even if API hiccups
        pass
    return render_template("index.html", history=history)


@APP.route("/api/status/<job_id>", methods=["GET"])
def proxy_status(job_id):
    try:
        resp = requests.get(f"{API_BASE}/status/{job_id}", timeout=10)
    except requests.RequestException:
        return ("{\"ok\": false, \"error\": {\"message\": \"upstream unreachable\"}}", 502, {
            "Content-Type": "application/json"
        })

    # Pass through JSON response (or a simple envelope) to the frontend
    body = resp.text
    return (body, resp.status_code, {"Content-Type": resp.headers.get("content-type", "application/json")})


@APP.route("/about", methods=["GET"])
def about():
    return render_template("about.html")

@APP.route("/capture", methods=["POST"])
def capture():
    raw_url = request.form.get("url", "")
    full_page = request.form.get("full_page") == "on"  # currently unused by API
    mobile_view = request.form.get("mobile_view") == "on"  # currently unused by API

    url = normalize_url(raw_url)
    if not url:
        flash("Please enter a valid URL (e.g., https://example.com).", "error")
        return redirect(url_for("index"))

    # Your current API expects {"urls": [...]}
    payload = {"urls": [url]}

    try:
        resp = requests.post(f"{API_BASE}/capture", json=payload, timeout=20)
    except requests.RequestException:
        flash("Capture API is unreachable.", "error")
        return redirect(url_for("index"))

    # Support both old and new response envelopes
    data = {}
    try:
        data = resp.json()
    except Exception:
        pass

    if resp.status_code not in (200, 202):
        err_msg = (
            data.get("error", {}).get("message")
            if isinstance(data.get("error"), dict)
            else data.get("error")
        ) or "Capture request failed"
        flash(err_msg, "error")
        return redirect(url_for("index"))

    job_id = data.get("job_id")
    if not job_id and isinstance(data.get("data"), dict):
        job_id = data["data"].get("job_id")

    status = data.get("status", "queued")
    if isinstance(data.get("data"), dict):
        status = data["data"].get("status", status)

    flash("Capture queued.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    DEBUG_MODE = os.getenv("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")
    APP.run(host="0.0.0.0", port=5050, debug=False)