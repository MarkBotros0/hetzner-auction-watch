"""Flask app for Vercel: exposes the auction check as an HTTP endpoint.

cron-job.org calls GET /api/check every minute with the header
"Authorization: Bearer <CRON_SECRET>"; each call runs one check and returns
its report. Run locally with `python app.py` (http://127.0.0.1:5000/api/check).
"""
import os
import traceback

from flask import Flask, Response, request

import watch

app = Flask(__name__)


def text(body, status=200):
    return Response(body, status=status, mimetype="text/plain")


@app.get("/")
def index():
    return text("Hetzner auction watch. GET /api/check runs one check.\n")


@app.route("/api/check", methods=["GET", "POST"])
def check():
    secret = os.environ.get("CRON_SECRET")
    if secret and request.headers.get("Authorization") != f"Bearer {secret}":
        return text("Unauthorized\n", 401)
    if not watch.REDIS_URL and os.environ.get("VERCEL"):
        # Without a state store every run would re-send the same alerts.
        return text("No state store: connect an Upstash Redis store to this project.\n", 500)
    try:
        ok, report = watch.main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return text("Check crashed:\n" + traceback.format_exc(), 500)
    return text(report, 200 if ok else 502)


if __name__ == "__main__":
    app.run(debug=True)
