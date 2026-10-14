"""Socket Unix: uma requisição JSON por linha, uma resposta JSON por linha."""
import json
import logging
import os
import socket
import socketserver
import threading
from pathlib import Path

log = logging.getLogger("sentinel_guard.server")


def handle_request(daemon, req: dict) -> dict:
    rid = req.get("id")
    cmd = req.get("cmd")
    args = req.get("args") or {}
    try:
        fn = getattr(daemon, f"cmd_{cmd}", None) if isinstance(cmd, str) else None
        if fn is None:
            raise ValueError(f"comando desconhecido: {cmd}")
        return {"id": rid, "ok": True, "result": fn(**args)}
    except Exception as e:
        return {"id": rid, "ok": False, "error": str(e)}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError("requisição deve ser objeto JSON")
                resp = handle_request(self.server.daemon, req)
            except Exception as e:
                resp = {"id": None, "ok": False, "error": f"requisição inválida: {e}"}
            self.wfile.write((json.dumps(resp) + "\n").encode())


class SocketServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, daemon):
        self.daemon = daemon
        self.path = Path(path)
        if self.path.exists():
            if _alive(self.path):
                raise RuntimeError(f"já existe um daemon em {self.path}")
            self.path.unlink()
        old = os.umask(0o177)
        try:
            super().__init__(str(self.path), _Handler)
        finally:
            os.umask(old)

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, name="socket", daemon=True).start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.path.unlink(missing_ok=True)


def _alive(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX)
    try:
        s.settimeout(1)
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def request(path: Path, cmd: str, args: dict | None = None, timeout: float = 30) -> dict:
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(str(path))
        s.sendall((json.dumps({"id": 1, "cmd": cmd, "args": args or {}}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)
