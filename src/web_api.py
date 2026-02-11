from flask import Flask, jsonify, request
import os
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import importlib.util

APP = Flask(__name__)

# Load the capture module from the script file (filename has a hyphen)
HERE = os.path.dirname(__file__)
MODULE_PATH = os.path.join(HERE, "web-capture.py")
spec = importlib.util.spec_from_file_location("web_capture", MODULE_PATH)
web_capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_capture)

# Thread pool for background capture jobs
executor = ThreadPoolExecutor(max_workers=2)
jobs = {}


@APP.route("/urls", methods=["GET"])
def list_urls():
    urls = web_capture.load_urls(os.path.join(HERE, "..", "data", "urls.json"))
    return jsonify({"urls": urls})


@APP.route("/capture", methods=["POST"])
def start_capture():
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")
    if not urls:
        urls = web_capture.load_urls(os.path.join(HERE, "..", "data", "urls.json"))

    job_id = uuid.uuid4().hex
    future = executor.submit(web_capture.capture_urls, urls)
    jobs[job_id] = future
    return jsonify({"job_id": job_id}), 202


@APP.route("/status/<job_id>", methods=["GET"])
def job_status(job_id):
    future = jobs.get(job_id)
    if future is None:
        return jsonify({"error": "job not found"}), 404
    if future.done():
        try:
            result = future.result()
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 200
        return jsonify({"status": "done", "result": result}), 200
    else:
        return jsonify({"status": "running"}), 200


@APP.route("/save_selected", methods=["POST"])
def save_selected():
    body = request.get_json(silent=True) or {}
    urls = body.get("urls")
    if not isinstance(urls, list):
        return jsonify({"error": "provide a list of urls"}), 400

    out_path = os.path.join(HERE, "..", "data", "selected_urls.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"selected_urls": urls}, f, indent=2)
    return jsonify({"saved": len(urls)})


if __name__ == "__main__":
    # Run simple dev server
    DEBUG_MODE = False
    APP.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE)
