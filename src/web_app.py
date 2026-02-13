from flask import Flask, render_template, request, redirect, url_for, flash
import os
import sqlite3
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

APP = Flask(__name__)
APP.secret_key = os.getenv("FRONTEND_SECRET_KEY", "dev-secret-change-me")

HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(HERE, "history.db")

# Point this to your existing capture API service
# Example (local): http://localhost:5000
# Example (docker compose service name): http://api:5000
API_BASE = os.getenv("WEB_CAPTURE_API_BASE", "http://localhost:5000")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS capture_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                full_page INTEGER NOT NULL DEFAULT 1,
                mobile_view INTEGER NOT NULL DEFAULT 0,
                job_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


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


def insert_history(url, full_page, mobile_view, job_id, status, error=None):
    ts = now_iso()
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO capture_history (url, full_page, mobile_view, job_id, status, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (url, int(full_page), int(mobile_view), job_id, status, error, ts, ts),
        )
        conn.commit()


def update_history_status(job_id, status, error=None):
    ts = now_iso()
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE capture_history
            SET status = ?, error = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (status, error, ts, job_id),
        )
        conn.commit()


def refresh_pending_jobs(limit=15):
    """
    Optional light polling on page load:
    checks latest queued/running jobs and updates their status in DB.
    """
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT job_id, status
            FROM capture_history
            WHERE job_id IS NOT NULL
              AND status IN ('queued', 'running')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    for row in rows:
        job_id = row["job_id"]
        try:
            resp = requests.get(f"{API_BASE}/status/{job_id}", timeout=8)
            if not resp.ok:
                continue
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            # Support your current API shape:
            # {"status":"running"} or {"status":"done","result":...} or {"status":"error","error":"..."}
            status = data.get("status")
            err = data.get("error")

            if status in ("running", "queued"):
                update_history_status(job_id, status)
            elif status == "done":
                update_history_status(job_id, "done")
            elif status == "error":
                update_history_status(job_id, "error", err or "capture failed")
        except Exception:
            # keep UI simple; don't fail page load if status check fails
            pass


@APP.route("/", methods=["GET"])
def index():
    refresh_pending_jobs()
    with db_conn() as conn:
        history = conn.execute(
            """
            SELECT id, url, full_page, mobile_view, job_id, status, error, created_at
            FROM capture_history
            ORDER BY id DESC
            LIMIT 50
            """
        ).fetchall()
    return render_template("index.html", history=history)


@APP.route("/about", methods=["GET"])
def about():
    return render_template("about.html")


@APP.route("/capture", methods=["POST"])
def capture():
    raw_url = request.form.get("url", "")
    full_page = request.form.get("full_page") == "on"
    mobile_view = request.form.get("mobile_view") == "on"

    url = normalize_url(raw_url)
    if not url:
        flash("Please enter a valid URL (e.g., https://example.com).", "error")
        return redirect(url_for("index"))

    # Your current API expects {"urls": [...]}
    payload = {"urls": [url]}

    try:
        resp = requests.post(f"{API_BASE}/capture", json=payload, timeout=20)
    except requests.RequestException:
        insert_history(url, full_page, mobile_view, None, "error", "API unreachable")
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
        insert_history(url, full_page, mobile_view, None, "error", err_msg)
        flash(err_msg, "error")
        return redirect(url_for("index"))

    job_id = data.get("job_id")
    if not job_id and isinstance(data.get("data"), dict):
        job_id = data["data"].get("job_id")

    status = data.get("status", "queued")
    if isinstance(data.get("data"), dict):
        status = data["data"].get("status", status)

    insert_history(url, full_page, mobile_view, job_id, status or "queued")
    flash("Capture queued.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    DEBUG_MODE = os.getenv("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")
    APP.run(host="0.0.0.0", port=5050, debug=False)
else:
    init_db()