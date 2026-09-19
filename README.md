# Web Snapshot

Web Snapshot captures full-page screenshots and HTML from one or more URLs. A
Flask API queues capture jobs, Playwright performs the browser work, and an
optional scheduler submits recurring jobs for monitored domains.

## Components

| Component | File | Purpose |
| --- | --- | --- |
| Capture worker | `src/web_capture.py` | Opens pages with Chromium and saves HTML and screenshots |
| API | `src/web_api.py` | Queues captures and stores job state in SQLite |
| Scheduler | `src/scheduler.py` | Runs recurring domain captures through the API |
| Web interface | `src/web_app.py` | Provides a small UI for one-off captures and job history |
| Sitemap scanner | `src/sitemap_scan.py` | Finds sitemap files through `robots.txt` and common paths |

Generated files are written below `data/`:

```text
data/
|-- app.db
|-- logs/scrape.log
`-- snapshots/<domain>/
    |-- html/
    `-- screenshots/
```

## Docker Deployment

Docker Compose runs the API, web interface, and scheduler. The `data` directory
is mounted into the containers, so the database and snapshots survive container
recreation.

Set `UID` and `GID` in `.env` to the host account that should own generated
files. For example:

```dotenv
UID=1000
GID=1000
```

Build and start the application:

```bash
docker compose up -d --build
```

The services are then available at:

- Web interface: `http://localhost:5050`
- Capture API: `http://localhost:5000`
- Scheduler: background service with no exposed port

Inspect service state and logs with:

```bash
docker compose ps
docker compose logs -f api scheduler
```

Stopping the containers does not remove `data/`:

```bash
docker compose down
```

The API has no authentication. Do not expose port `5000` directly to the public
internet; place it behind an authenticated reverse proxy or restrict it at the
network layer.

## Scheduler

The scheduler stores its configuration in the `monitored_domains` table in
`data/app.db`. On startup it creates this table if needed, checks for due
domains every 60 seconds, and submits their URL lists to `POST /capture`.

A new monitor is due immediately. After the API accepts a job, the scheduler
records the job ID and run time. If submission fails, it records the error and
retries on a later scheduler pass.

### Capture modes

- With no discovery flags, only the homepage is captured.
- `--include-pages` also captures same-domain links found in the homepage HTML.
- `--scan-sitemap` adds same-domain URLs from sitemap files and automatically
  enables page capture.
- A run is limited to 100 URLs by default. Use `--max-pages` when starting the
  scheduler to change that process-wide limit.

Page discovery is intentionally simple. It does not perform a recursive site
crawl, and client-rendered links may not be present in the homepage HTML.

### Commands

| Command | Description |
| --- | --- |
| `run` | Run continuously; this is the default when no command is provided |
| `run --once` | Submit currently due monitors and exit |
| `init` | Create the scheduler table without starting the scheduler |
| `add DOMAIN` | Add a monitor or update an existing one |
| `list` | Print monitor configuration and latest run state as JSON |
| `remove DOMAIN` | Delete a monitor |

### Configure monitors with Docker

Add a homepage-only monitor using the default 24-hour interval:

```bash
docker compose exec scheduler python3 src/scheduler.py add example.com
```

Capture homepage links every six hours:

```bash
docker compose exec scheduler python3 src/scheduler.py add example.com \
  --interval-hours 6 \
  --include-pages
```

Include sitemap URLs as well:

```bash
docker compose exec scheduler python3 src/scheduler.py add example.com \
  --interval-hours 24 \
  --include-pages \
  --scan-sitemap
```

Adding an existing domain updates its interval and options. List or remove
monitors with:

```bash
docker compose exec scheduler python3 src/scheduler.py list
docker compose exec scheduler python3 src/scheduler.py remove example.com
```

The long-running scheduler notices database changes on its next polling pass.

### Run the scheduler locally

The API must already be running. Start the scheduler in the foreground with:

```bash
python3 src/scheduler.py
```

Run currently due monitors once and exit:

```bash
python3 src/scheduler.py --once
```

Useful runtime options are:

| Option | Default | Description |
| --- | --- | --- |
| `--api` | `http://localhost:5000` | Capture API base URL |
| `--db` | `data/app.db` | SQLite database path |
| `--poll-seconds` | `60` | Seconds between due checks |
| `--max-pages` | `100` | Maximum URLs submitted per domain |
| `--once` | off | Check once instead of running continuously |

`WEB_SNAPSHOT_API` or `WEB_CAPTURE_API_BASE` can set the API URL. `DB_PATH` can
set the database path. Command-line options take precedence.

Run only one scheduler process against a database. The current implementation
does not claim rows with a distributed lock, so multiple scheduler instances
could submit duplicate jobs.

## Capture API

Start the API locally with:

```bash
python3 src/web_api.py
```

It listens on `0.0.0.0:5000` and runs capture work asynchronously with up to two
background workers. Successful responses contain `"ok": true`; errors contain
an `error` object.

### Start a capture

`POST /capture` accepts a JSON array of URLs:

```bash
curl -X POST http://localhost:5000/capture \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://example.com/","https://example.com/about"]}'
```

The API responds with HTTP `202`:

```json
{
  "job_id": "e4f8d2c93c8c4d9987acb6810af0c291",
  "ok": true,
  "status": "queued"
}
```

If `urls` is missing or empty, the API reads `scrape_urls` from
`data/urls.json`:

```json
{
  "scrape_urls": [
    "https://example.com/"
  ]
}
```

### Check a job

```bash
curl http://localhost:5000/status/JOB_ID
```

Status progresses through `queued`, `running`, and `done`. A failed job has
status `error` and includes an error message. Completed jobs include a `result`
array with the status and output paths for each URL.

### List jobs

```bash
curl 'http://localhost:5000/jobs?limit=50'
```

`limit` defaults to 50 and is capped at 200.

### Other endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/urls` | Return URLs from `data/urls.json` |
| `POST` | `/save_selected` | Save `{"urls": [...]}` to SQLite and `data/selected_urls.json` |

## Local Setup

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

Start the API and web interface in separate terminals:

```bash
python3 src/web_api.py
python3 src/web_app.py
```

The local scheduler and API use the same `data/app.db` by default. SQLite stores
API jobs, selected URLs, and scheduler monitors in separate tables.

For a one-off capture through the API, use:

```bash
python3 src/capture_once.py https://example.com/
```

## Troubleshooting

If Chromium is missing, install it for the active Python environment:

```bash
playwright install chromium
```

If Docker cannot write snapshots or SQLite files, verify the `UID` and `GID` in
`.env` and the ownership of `data/`.

If the scheduler reports that the API is unreachable, confirm the API is
running and set the correct URL with `--api` or `WEB_SNAPSHOT_API`.
