#!/usr/bin/env python3
"""Tiny static server for the lab site (stdlib only).

Why not `python3 -m http.server`? It cannot send redirects, and the lab needs a redirect chain.
This server does the same job and also:
  * reads site/redirects.json on every request (301 responses), so fixes show up without a restart
  * replaces {{BASE}} in HTML with the real origin, so canonicals are absolute and port-independent
  * serves "/" as index.html and returns a real 404 for unknown paths

Run it:  python3 server.py [--port 8765] [--site site]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def make_handler(site_dir: Path):
    site_dir = site_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "SeoLab/1.0"

        def log_message(self, *args):  # keep test output clean
            pass

        def _redirects(self):
            path = site_dir / "redirects.json"
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

        def _origin(self):
            return f"http://{self.headers.get('Host') or '127.0.0.1'}"

        def _send(self, code, body=b"", content_type="text/html; charset=utf-8", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self):
            path = urlsplit(self.path).path or "/"
            redirects = self._redirects()
            if path in redirects:
                target = redirects[path]
                if target.startswith("/"):
                    target = self._origin() + target
                return self._send(301, b"", headers={"Location": target})

            rel = "index.html" if path == "/" else path.lstrip("/")
            candidate = (site_dir / rel).resolve()
            inside = site_dir == candidate or site_dir in candidate.parents
            if not inside or not candidate.is_file() or candidate.name == "redirects.json":
                return self._send(404, b"<!DOCTYPE html><html><head><title>Not found</title></head>"
                                       b"<body><p>404 Not Found</p></body></html>")

            body = candidate.read_bytes()
            ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if candidate.suffix == ".html":
                body = body.decode("utf-8").replace("{{BASE}}", self._origin()).encode("utf-8")
                ctype = "text/html; charset=utf-8"
            self._send(200, body, ctype)

        do_GET = _handle
        do_HEAD = _handle

    return Handler


def start_server(site_dir, port=0, host="127.0.0.1"):
    """Start the lab server in a background thread. Returns (server, base_url). port=0 picks a free port."""
    httpd = ThreadingHTTPServer((host, port), make_handler(Path(site_dir)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://{host}:{httpd.server_address[1]}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--site", default=str(Path(__file__).parent / "site"))
    args = parser.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Path(args.site)))
    print(f"Serving {args.site} at http://127.0.0.1:{args.port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
