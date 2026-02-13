from flask import Flask, jsonify, request
import os
import json
import uuid
import sqlite3
import tempfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import importlib.util

APP = Flask(__name__)

# ---------- Paths ----------
HERE = os.path.dirname(__file__)
DATA_DIR = os.path.join(HERE, "..", "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")
URLS_PATH = os.path.join(DATA_DIR, "urls.json")
SELECTED_URLS_PATH = os.path.join(DATA_DIR, "selected_urls.json")

# ---------- Load web-capture.py (hyphenated filename) ----------
MODULE_PATH = os.path.join(HERE, "web_capture.py")
spec = importlib.util.spec_from_file_location("web_capture", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load module at {MODULE_PATH}")
web_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_capture)

# ---------- Background workers ----------
executor = ThreadPoolExecutor(max_workers=2)


# ---------- Helpers ----------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                urls_json TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS selected_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                urls_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Mark stale in-flight jobs if API process restarted
        conn.execute(
            """
            UPDATE jobs
            SET status = 'error',
                error_text = ?,
                updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            ("service restarted before completion", now_utc_iso()),
        )
        conn.commit()


def api_ok(data=None, status=200):
    payload = {"ok": True}
    if data is not None:
        payload.update(data)
    return jsonify(payload), status


def api_error(message: str, status=400, code=None):
    payload = {"ok": False, "error": {"message": message}}
    if code:
        payload["error"]["code"] = code
    return jsonify(payload), status


def validate_urls(urls):
    if not isinstance(urls, list):
        return None, "provide a list of urls"
    cleaned = []
    for u in urls:
        if not isinstance(u, str) or not u.strip():
            return None, "all urls must be non-empty strings"
        cleaned.append(u.strip())
    return cleaned, None


def atomic_write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------- Job state functions ----------
def create_job(urls):
    job_id = uuid.uuid4().hex
    ts = now_utc_iso()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, status, urls_json, result_json, error_text, created_at, updated_at)
            VALUES (?, 'queued', ?, NULL, NULL, ?, ?)
            """,
            (job_id, json.dumps(urls), ts, ts),
        )
        conn.commit()
    return job_id


def mark_running(job_id):
    with db_connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', updated_at=? WHERE job_id=?",
            (now_utc_iso(), job_id),
        )
        conn.commit()


def mark_done(job_id, result):
    with db_connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='done', result_json=?, error_text=NULL, updated_at=? WHERE job_id=?",
            (json.dumps(result), now_utc_iso(), job_id),
        )
        conn.commit()


def mark_error(job_id, err_text):
    with db_connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='error', error_text=?, updated_at=? WHERE job_id=?",
            (str(err_text), now_utc_iso(), job_id),
        )
        conn.commit()


def get_job(job_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT job_id, status, result_json, error_text, created_at, updated_at FROM jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
    return row


def run_capture_job(job_id, urls):
    mark_running(job_id)
    try:
        result = web_capture.capture_urls(urls)
        mark_done(job_id, result)
    except Exception as e:
        mark_error(job_id, e)


# ---------- Routes ----------
@APP.route("/urls", methods=["GET"])
def list_urls():
    try:
        urls = web_capture.load_urls(URLS_PATH)
        return api_ok({"urls": urls})
    except Exception as e:
        return api_error(str(e), status=500, code="URL_LOAD_ERROR")


@APP.route("/capture", methods=["POST"])
def start_capture():
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")

    if urls is not None:
        urls, err = validate_urls(urls)
        if err:
            return api_error(err, status=400, code="INVALID_INPUT")

    # Keep your old behavior: fallback to urls.json if urls missing or empty
    if not urls:
        try:
            urls = web_capture.load_urls(URLS_PATH)
        except Exception as e:
            return api_error(str(e), status=500, code="URL_LOAD_ERROR")

    job_id = create_job(urls)
    executor.submit(run_capture_job, job_id, urls)

    return api_ok({"job_id": job_id, "status": "queued"}, status=202)


@APP.route("/status/<job_id>", methods=["GET"])
def job_status(job_id):
    row = get_job(job_id)
    if row is None:
        return api_error("job not found", status=404, code="NOT_FOUND")

    status = row["status"]
    payload = {
        "job_id": row["job_id"],
        "status": status,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

    if status == "done" and row["result_json"]:
        payload["result"] = json.loads(row["result_json"])
    elif status == "error":
        payload["error"] = row["error_text"] or "unknown error"

    return api_ok(payload)


@APP.route("/save_selected", methods=["POST"])
def save_selected():
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")
    urls, err = validate_urls(urls)
    if err:
        return api_error(err, status=400, code="INVALID_INPUT")

    # Persist in SQLite
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO selected_urls (urls_json, created_at) VALUES (?, ?)",
            (json.dumps(urls), now_utc_iso()),
        )
        conn.commit()

    # Keep compatibility with your existing selected_urls.json flow
    atomic_write_json(SELECTED_URLS_PATH, {"selected_urls": urls})

    return api_ok({"saved": len(urls)})


# ---------- Main ----------
init_db()

if __name__ == "__main__":
    DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() == "true"
    APP.run(host="0.0.0.0", port=5000, debug=False)