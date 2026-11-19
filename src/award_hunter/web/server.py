"""Local HTTP server for the browser UI and its JSON API (stdlib http.server)."""
import hmac
import ipaddress
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_BODY = 8 * 1024 * 1024


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def make_handler(app, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "award-hunter"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got, f"Bearer {token}")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in STATIC:
                name, ctype = STATIC[path]
                return self._send(200, resources.files("award_hunter.web").joinpath(name).read_bytes(), ctype)
            if path == "/api/status":
                if not self._authorized():
                    return self._send(401, {"error": "token required"})
                return self._send(200, app.status())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._send(415, {"error": "JSON only"})     # blocks cross-site form posts
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            m = re.fullmatch(r"/api/awards/(\w+)/(\w+)", self.path)
            try:
                if m:
                    return self._send(200, app.action(m.group(1), m.group(2), payload))
                if self.path == "/api/settings":
                    return self._send(200, app.update_settings(payload))
                if self.path == "/api/refresh-data":
                    threading.Thread(target=app.refresh_data, kwargs={"force": True}, daemon=True).start()
                    return self._send(200, {"ok": True, "message": "refresh started"})
            except KeyError:
                return self._send(404, {"error": "unknown award"})
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                app.log(f"web action error: {e!r}")
                return self._send(500, {"error": "internal error"})
            self._send(404, {"error": "not found"})

    return Handler


def load_token(cfg):
    tf = cfg["web"]["token_file"]
    if not tf:
        return ""
    with open(os.path.expanduser(tf)) as fh:
        return fh.read().strip()


def make_server(app, bind=None, port=None):
    w = app.cfg["web"]
    bind = bind if bind is not None else w["bind"]
    port = port if port is not None else w["port"]
    token = load_token(app.cfg)
    if not is_loopback(bind) and not token:
        raise SystemExit(f"web.bind = {bind!r} is reachable from the network: set web.token_file first")
    return ThreadingHTTPServer((bind, port), make_handler(app, token))


def serve(app):
    srv = make_server(app)
    host, port = srv.server_address[:2]
    app.log(f"web UI: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/")
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
