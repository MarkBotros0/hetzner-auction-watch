"""Vercel function: runs one auction check per request.

cron-job.org calls GET /api/check every minute with the header
"Authorization: Bearer <CRON_SECRET>". The response is the check's report.
"""
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watch  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        secret = os.environ.get("CRON_SECRET")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            return self.reply(401, "Unauthorized\n")
        if not watch.REDIS_URL:
            # Without a state store every run would re-send the same alerts.
            return self.reply(500, "No state store: connect an Upstash Redis store to this project.\n")
        try:
            ok, report = watch.main()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return self.reply(500, "Check crashed:\n" + traceback.format_exc())
        self.reply(200 if ok else 502, report)

    do_POST = do_GET

    def reply(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
