import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from .config import Rules

log = logging.getLogger("sentinel_guard.alerts")

DVR_PADDING = timedelta(seconds=15)


def _annotate(frame, bbox):
    img = frame.copy()
    if bbox:
        h, w = img.shape[:2]
        x, y, bw, bh = bbox
        cv2.rectangle(img, (int(x * w), int(y * h)), (int((x + bw) * w), int((y + bh) * h)), (0, 180, 255), 2)
    return img


class AlertManager:
    """Segmenta movimento em eventos, aplica regras de notificação e guarda histórico.

    Cada evento guarda início/fim no relógio local (mesmo relógio exibido pelo DVR) para
    localizar o trecho na gravação do DVR.
    """

    def __init__(self, data_dir: Path, rules: Rules, notifiers, clock=time.monotonic, now=None):
        self.data_dir = Path(data_dir)
        (self.data_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        self.rules = rules
        self.notifiers = notifiers  # callables (camera_name, body) -> None
        self._clock = clock
        self._now = now or (lambda: datetime.now().astimezone())
        self._open: dict[int, dict] = {}
        self._last_notified: dict[int, float] = {}
        self._sent_times: list[float] = []
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "events.db", check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "camera_id INTEGER, camera_name TEXT, start_ts TEXT NOT NULL, end_ts TEXT, "
            "peak_area_pct REAL, snapshot TEXT, peak_snapshot TEXT, "
            "notified INTEGER DEFAULT 0, reason TEXT, error TEXT)")
        self._db.commit()

    # --- ciclo do evento ---
    def motion(self, camera, frame, area_pct: float, bbox) -> None:
        """Chamado a cada frame com movimento sustentado."""
        now = self._clock()
        with self._lock:
            ev = self._open.get(camera.id)
            if ev is None:
                ts = self._now()
                snap = self.data_dir / "snapshots" / f"cam{camera.id}_{ts:%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(snap), _annotate(frame, bbox), [cv2.IMWRITE_JPEG_QUALITY, 85])
                cur = self._db.execute(
                    "INSERT INTO events (camera_id, camera_name, start_ts, snapshot, peak_area_pct) "
                    "VALUES (?,?,?,?,?)",
                    (camera.id, camera.name, ts.isoformat(timespec="seconds"), str(snap), area_pct))
                self._db.commit()
                ev = self._open[camera.id] = {
                    "id": cur.lastrowid, "start": now, "last": now, "peak": area_pct, "decided": False,
                    "peak_frame": frame, "peak_bbox": bbox, "camera": camera, "start_ts": ts}
            ev["last"] = now
            if area_pct > ev["peak"]:
                ev.update(peak=area_pct, peak_frame=frame, peak_bbox=bbox)
            if not ev["decided"] and now - ev["start"] >= self.rules.min_event_seconds:
                ev["decided"] = True
                self._decide(ev, now)

    def tick(self) -> None:
        """Fecha eventos sem movimento há event_gap_seconds."""
        now = self._clock()
        with self._lock:
            for cid in [c for c, e in self._open.items() if now - e["last"] >= self.rules.event_gap_seconds]:
                self._close(cid)

    def close_all(self) -> None:
        with self._lock:
            for cid in list(self._open):
                self._close(cid)

    def _close(self, cid: int) -> None:
        ev = self._open.pop(cid)
        end_ts = ev["start_ts"] + timedelta(seconds=max(0.0, ev["last"] - ev["start"]))
        peak_snap = self.data_dir / "snapshots" / f"cam{cid}_{ev['start_ts']:%Y%m%d_%H%M%S}_peak.jpg"
        cv2.imwrite(str(peak_snap), _annotate(ev["peak_frame"], ev["peak_bbox"]), [cv2.IMWRITE_JPEG_QUALITY, 85])
        sets = "end_ts=?, peak_area_pct=?, peak_snapshot=?"
        vals = [end_ts.isoformat(timespec="seconds"), ev["peak"], str(peak_snap)]
        if not ev["decided"]:
            sets += ", reason=?"
            vals.append("curto")
        self._db.execute(f"UPDATE events SET {sets} WHERE id=?", (*vals, ev["id"]))
        self._db.commit()

    # --- regras ---
    def _decide(self, ev: dict, now: float) -> None:
        cam = ev["camera"]
        reason = None
        last = self._last_notified.get(cam.id)
        self._sent_times = [t for t in self._sent_times if now - t < 3600]
        if not self.rules.notify:
            reason = "somente_registro"
        elif last is not None and now - last < self.rules.cooldown_seconds:
            reason = "cooldown"
        elif len(self._sent_times) >= self.rules.max_notifications_per_hour:
            reason = "limite_hora"
        if reason:
            self._db.execute("UPDATE events SET reason=? WHERE id=?", (reason, ev["id"]))
            self._db.commit()
            return
        self._last_notified[cam.id] = now
        self._sent_times.append(now)
        body = f"Movimento detectado: {cam.name} às {ev['start_ts']:%H:%M:%S}"
        threading.Thread(target=self._notify, args=(ev["id"], cam.name, body), daemon=True).start()

    def _notify(self, event_id: int, name: str, body: str) -> None:
        errors, ok = [], 0
        for n in self.notifiers:
            try:
                n(name, body)
                ok += 1
            except Exception as e:
                log.error("notificação falhou: %s", e)
                errors.append(str(e)[:200])
        with self._lock:
            self._db.execute("UPDATE events SET notified=?, error=? WHERE id=?",
                             (1 if ok else 0, "; ".join(errors) or None, event_id))
            self._db.commit()

    # --- consulta ---
    def list_events(self, limit: int = 50, since: str | None = None) -> list[dict]:
        q = ("SELECT id, camera_id, camera_name, start_ts, end_ts, peak_area_pct, snapshot, "
             "peak_snapshot, notified, reason, error FROM events")
        args: list = []
        if since:
            q += " WHERE start_ts >= ?"
            args.append(since)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        keys = ("id", "cameraId", "cameraName", "start", "end", "peakAreaPct", "snapshot",
                "peakSnapshot", "notified", "reason", "error")
        out = []
        for r in rows:
            e = dict(zip(keys, r))
            e["dvrWindow"] = self._dvr_window(e["start"], e["end"])
            out.append(e)
        return out

    def snapshot_path(self, event_id: int, kind: str = "peak") -> Path | None:
        """Caminho do snapshot de um evento; só devolve arquivos dentro de data_dir/snapshots."""
        col = "peak_snapshot" if kind == "peak" else "snapshot"
        with self._lock:
            row = self._db.execute(f"SELECT {col} FROM events WHERE id=?", (event_id,)).fetchone()
        if not row or not row[0]:
            return None
        p = Path(row[0]).resolve()
        root = (self.data_dir / "snapshots").resolve()
        return p if p.is_file() and root in p.parents else None

    @staticmethod
    def _dvr_window(start: str, end: str | None):
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end) if end else s
        return [(s - DVR_PADDING).strftime("%H:%M:%S"), (e + DVR_PADDING).strftime("%H:%M:%S")]
