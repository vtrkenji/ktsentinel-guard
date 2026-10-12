import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

log = logging.getLogger("sentinel_guard.alerts")


class AlertManager:
    """Cooldown por câmera, snapshot, histórico SQLite e envio assíncrono."""

    def __init__(self, data_dir: Path, cooldown: float, notifiers, clock=time.monotonic):
        self.data_dir = Path(data_dir)
        (self.data_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        self.cooldown = cooldown
        self.notifiers = notifiers  # callables (camera_name, body) -> None
        self._clock = clock
        self._last: dict[int, float] = {}
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "events.db", check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT NOT NULL, camera_id INTEGER, camera_name TEXT, snapshot TEXT, "
            "notified INTEGER DEFAULT 0, error TEXT)")
        self._db.commit()

    def trigger(self, camera, frame, wait: bool = False) -> int | None:
        """Registra alerta se fora do cooldown. Retorna id do alerta ou None."""
        now = self._clock()
        with self._lock:
            last = self._last.get(camera.id)
            if last is not None and now - last < self.cooldown:
                return None
            self._last[camera.id] = now
            ts = datetime.now().astimezone()
            snap = self.data_dir / "snapshots" / f"cam{camera.id}_{ts:%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(str(snap), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            cur = self._db.execute(
                "INSERT INTO alerts (ts, camera_id, camera_name, snapshot) VALUES (?,?,?,?)",
                (ts.isoformat(timespec="seconds"), camera.id, camera.name, str(snap)))
            self._db.commit()
            alert_id = cur.lastrowid
        t = threading.Thread(target=self._notify, args=(alert_id, camera.name), daemon=True)
        t.start()
        if wait:
            t.join()
        return alert_id

    def _notify(self, alert_id: int, name: str) -> None:
        body = f"Movimento detectado: {name}"
        errors = []
        ok = 0
        for n in self.notifiers:
            try:
                n(name, body)
                ok += 1
            except Exception as e:
                log.error("notificação falhou: %s", e)
                errors.append(str(e)[:200])
        with self._lock:
            self._db.execute("UPDATE alerts SET notified=?, error=? WHERE id=?",
                             (1 if ok else 0, "; ".join(errors) or None, alert_id))
            self._db.commit()

    def list_alerts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, camera_id, camera_name, snapshot, notified, error "
                "FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ("id", "ts", "cameraId", "cameraName", "snapshot", "notified", "error")
        return [dict(zip(keys, r)) for r in rows]
