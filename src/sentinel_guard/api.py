"""API HTTP (JSON) para controle remoto. Desligada por padrão; autenticação por token Bearer.

GET  /                     página web (histórico + zonas no notebook; pede o token)
GET  /v1/status            GET  /v1/cameras
POST /v1/arm               POST /v1/disarm
POST /v1/test-notification
GET/PUT /v1/settings        ajustes (sensibilidade, avisos, retenção)
GET/PUT /v1/schedule        agenda: {"enabled":true,"windows":[{"days":[0,1],"start":"11:30","end":"05:00"}]}
GET  /v1/events?limit=&since=&notified=1
GET  /v1/snapshot?id=N&kind=peak|start   (JPEG)
GET  /v1/preview?id=N                    (JPEG do stream, um frame)
PUT  /v1/cameras/{id}/geometry           zonas de scan, ignore, tripwire, loiter
"""
import hmac
import json
import logging
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import re

log = logging.getLogger("sentinel_guard.api")
WEB_PAGE = Path(__file__).with_name("web.html")

ROUTES = {
    ("GET", "/v1/status"): "status",
    ("GET", "/v1/cameras"): "list_cameras",
    ("POST", "/v1/arm"): "arm",
    ("POST", "/v1/disarm"): "disarm",
    ("POST", "/v1/test-notification"): "test_notification",
}
GEOM_RE = re.compile(r"^/v1/cameras/(\d+)/geometry$")


MAX_FAILS = 5
LOCKOUT_SECONDS = 300


def make_handler(daemon, token: str, trusted_proxies=frozenset({"127.0.0.1"})):
    fails: dict[str, list] = {}  # ip -> [contagem, bloqueado_até]
    fails_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "sentinel-guard"

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode())

        def _authorized(self) -> bool:
            got = self.headers.get("Authorization", "")
            return got.startswith("Bearer ") and hmac.compare_digest(got[7:].strip(), token)

        def _route(self, method):
            if method == "GET" and urlparse(self.path).path in ("/", "/index.html"):
                return self._send(200, WEB_PAGE.read_bytes(), "text/html; charset=utf-8")  # sem dados; o token vai no JS
            ip = self.client_address[0]
            if ip in trusted_proxies and self.headers.get("X-Forwarded-For"):
                ip = self.headers["X-Forwarded-For"].split(",")[-1].strip()  # IP real, anexado pelo proxy local
            with fails_lock:
                rec = fails.get(ip)
                if rec and rec[1] > time.monotonic():
                    return self._json(429, {"ok": False, "error": "muitas tentativas; aguarde"})
            if not self._authorized():
                with fails_lock:
                    rec = fails.setdefault(ip, [0, 0.0])
                    rec[0] += 1
                    if rec[0] >= MAX_FAILS:
                        rec[:] = [0, time.monotonic() + LOCKOUT_SECONDS]
                return self._json(401, {"ok": False, "error": "não autorizado"})
            with fails_lock:
                fails.pop(ip, None)
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if method == "GET" and url.path == "/v1/events":
                    since = q.get("since")
                    res = daemon.cmd_list_events(limit=min(int(q.get("limit", 50)), 500), since=since,
                                              only_notified=q.get("notified") == "1")
                elif method == "GET" and url.path == "/v1/snapshot":
                    kind = "start" if q.get("kind") == "start" else "peak"
                    path = daemon.alerts.snapshot_path(int(q["id"]), kind)
                    if path is None:
                        return self._json(404, {"ok": False, "error": "snapshot não encontrado"})
                    return self._send(200, path.read_bytes(), "image/jpeg")
                elif method == "GET" and url.path == "/v1/preview":
                    jpeg = daemon.cmd_preview(q["id"])
                    return self._send(200, jpeg, "image/jpeg")
                elif method == "PUT" and (m := GEOM_RE.match(url.path)):
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 65536:
                        return self._json(413, {"ok": False, "error": "corpo grande demais"})
                    res = daemon.cmd_set_geometry(m.group(1), json.loads(self.rfile.read(n) or b"{}"))
                elif method == "GET" and url.path == "/v1/schedule":
                    res = daemon.cmd_get_schedule()
                elif method == "GET" and url.path == "/v1/settings":
                    res = daemon.cmd_get_settings()
                elif method == "PUT" and url.path == "/v1/settings":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 65536:
                        return self._json(413, {"ok": False, "error": "corpo grande demais"})
                    res = daemon.cmd_set_settings(json.loads(self.rfile.read(n) or b"{}"))
                elif method == "PUT" and url.path == "/v1/schedule":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 65536:
                        return self._json(413, {"ok": False, "error": "corpo grande demais"})
                    body = json.loads(self.rfile.read(n) or b"{}")
                    res = daemon.cmd_set_schedule(body["windows"], body.get("enabled", True))
                elif (method, url.path) in ROUTES:
                    res = getattr(daemon, f"cmd_{ROUTES[(method, url.path)]}")()
                else:
                    return self._json(404, {"ok": False, "error": "rota desconhecida"})
            except (KeyError, ValueError) as e:
                return self._json(400, {"ok": False, "error": f"parâmetro inválido: {e}"})
            except Exception as e:
                log.exception("erro na API")
                return self._json(500, {"ok": False, "error": str(e)})
            self._json(200, {"ok": True, "result": res})

        def do_GET(self):
            self._route("GET")

        def do_PUT(self):
            self._route("PUT")

        def do_POST(self):
            self._route("POST")

    return Handler


class ApiServer:
    def __init__(self, cfg, daemon):
        path = cfg.token_file
        if not path or not path.exists():
            raise RuntimeError(f"token da API não encontrado: rode 'sentinel-guard gen-token' ({path})")
        from .config import check_private
        check_private(path)
        token = path.read_text().strip()
        if len(token) < 20:
            raise RuntimeError("token da API muito curto")
        self._httpd = ThreadingHTTPServer((cfg.bind, cfg.port), make_handler(daemon, token, frozenset({"127.0.0.1", cfg.bind})))
        self._httpd.daemon_threads = True
        self.address = self._httpd.server_address

    def start(self) -> None:
        threading.Thread(target=self._httpd.serve_forever, name="api", daemon=True).start()
        log.info("API HTTP em %s:%s", *self.address)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
