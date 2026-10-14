import logging
import os
import threading
import time

import cv2

from .camera import Camera, redact_url
from .config import DetectionConfig
from .detector import MotionDetector
from .person import load_person_detector
from .tracker import Tracker, crossing, point_in_polygon

# TCP e timeout de 5s; precisa estar no ambiente antes do primeiro VideoCapture
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000")

log = logging.getLogger("sentinel_guard.capture")


class CameraWorker(threading.Thread):
    def __init__(self, camera: Camera, det_cfg: DetectionConfig, alerts, is_armed,
                 capture_factory=cv2.VideoCapture, max_backoff: float = 30.0):
        super().__init__(name=f"cam-{camera.id}", daemon=True)
        self.camera = camera
        self.det_cfg = det_cfg
        self.alerts = alerts
        self.is_armed = is_armed
        self._factory = capture_factory
        self._max_backoff = max_backoff
        self._stop_evt = threading.Event()
        self.state = "connecting"
        self.last_frame_at: float | None = None
        self.last_error: str | None = None
        self.reconnects = 0
        self.motion_events = 0
        self._person = None
        self.person_model = "off" if not det_cfg.person_filter else "pending"
        self._preview_lock = threading.Lock()
        self._preview_jpeg = None
        self._want_preview = threading.Event()
        self._preview_ready = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def status(self) -> dict:
        age = None if self.last_frame_at is None else round(time.monotonic() - self.last_frame_at, 1)
        return {**self.camera.to_json(), "state": self.state, "lastFrameAgeSeconds": age,
                "lastError": self.last_error, "reconnects": self.reconnects, "motionEvents": self.motion_events,
                "usingSecondary": bool(self.camera.rtsp_url_secondary),
                "personModel": self.person_model, "analytics": self.camera.analytics}

    def _detector(self) -> MotionDetector:
        c, d = self.camera, self.det_cfg
        return MotionDetector(
            sensitivity=c.sensitivity or d.sensitivity,
            min_area_pct=c.min_area_pct if c.min_area_pct is not None else d.min_area_pct,
            min_consecutive_frames=c.min_consecutive_frames or d.min_consecutive_frames,
            history=d.history, warmup_frames=int(d.warmup_seconds * d.fps),
            ignore_zones=c.ignore_zones, include_zones=c.include_zones)

    def preview_jpeg(self, timeout: float = 5.0) -> bytes | None:
        """Pede um JPEG do stream (só gasta um frame). None se a câmera não responder."""
        self._preview_ready.clear()
        self._want_preview.set()
        if not self._preview_ready.wait(timeout):
            with self._preview_lock:
                return self._preview_jpeg
        with self._preview_lock:
            return self._preview_jpeg

    def _store_preview(self, frame) -> None:
        h, w = frame.shape[:2]
        img = frame
        if w > 960:
            img = cv2.resize(frame, (960, int(h * 960 / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return
        with self._preview_lock:
            self._preview_jpeg = buf.tobytes()
        self._preview_ready.set()
        self._want_preview.clear()

    def _person_detector(self):
        d = self.det_cfg
        if not d.person_filter:
            self.person_model = "off"
            return None
        det, status = load_person_detector(d.person_model_dir, d.person_confidence)
        self.person_model = status
        if status != "ok":
            log.error("[%s] modelo de pessoa %s em %s; fail-closed (sem push até o modelo carregar)",
                      self.camera.name, status, d.person_model_dir)
        return det

    def _analyze(self, frame, now: float, tracker: Tracker) -> None:
        """Tripwire e loitering: roda o detector de pessoas e rastreia, independente do MOG2."""
        cam = self.camera
        for t in tracker.update(self._person.detect_all(frame), now):
            if t.hits < self.det_cfg.person_min_hits:
                continue
            for i, w in enumerate(cam.tripwires):
                if i in t.wires_fired or t.prev_foot is None:
                    continue
                d = crossing(w["line"][0], w["line"][1], t.prev_foot, t.foot)
                if d and w["direction"] in ("both", d):
                    t.wires_fired.add(i)
                    self.alerts.rule_event(cam, frame, t.box, "tripwire", f"Pessoa cruzou {w['name']}")
            in_zone = not cam.loiter_zone or point_in_polygon(t.foot, cam.loiter_zone)
            if cam.loiter_seconds and in_zone and not t.loiter_fired and now - t.first_seen >= cam.loiter_seconds:
                t.loiter_fired = True
                self.alerts.rule_event(cam, frame, t.box, "loiter", "Pessoa parada/rondando")

    def run(self) -> None:
        backoff = 1.0
        while not self._stop_evt.is_set():
            self.state = "connecting"
            cap = self._factory(self.camera.detection_url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self._fail("não foi possível abrir o stream", backoff)
                backoff = min(backoff * 2, self._max_backoff)
                cap.release()
                continue
            backoff = 1.0
            self.state = "online"
            self.last_error = None
            try:
                self._read_loop(cap)
            finally:
                cap.release()
            if not self._stop_evt.is_set():
                self._fail("stream interrompido", 1.0)
        self.state = "stopped"

    def _fail(self, msg: str, wait: float) -> None:
        self.state = "offline"
        self.last_error = msg
        self.reconnects += 1
        log.warning("[%s] %s; nova tentativa em %.0fs (%s)", self.camera.name, msg, wait,
                    redact_url(self.camera.detection_url))
        self._stop_evt.wait(wait)

    def _read_loop(self, cap) -> None:
        det = self._detector()
        if self._person is None and self.det_cfg.person_filter:
            self._person = self._person_detector()
        person_ok = self._person is not None
        if self.det_cfg.person_filter and not person_ok:
            self.state = "degraded"
            self.last_error = self.last_error or "modelo de pessoa ausente; sem avisos"
        last_person_check = 0.0
        tracker = Tracker()
        hits = 0
        last_analytics = 0.0
        interval = 1.0 / self.det_cfg.fps
        next_due = 0.0
        was_armed = False
        while not self._stop_evt.is_set():
            if not cap.grab():
                return
            now = time.monotonic()
            self.last_frame_at = now
            armed = self.is_armed()
            need_frame = armed or self._want_preview.is_set()
            if not need_frame:
                if now < next_due:
                    continue
                was_armed = False
                self.alerts.tick()
                next_due = now + 0.5
                continue
            if now < next_due and not self._want_preview.is_set():
                continue
            ok, frame = cap.retrieve()
            if not ok:
                return
            if self._want_preview.is_set():
                self._store_preview(frame)
            if not armed:
                was_armed = False
                next_due = now + 0.5
                continue
            self.alerts.tick()
            if not was_armed:
                det.reset()
                was_armed = True
            next_due = now + interval
            if person_ok and self.camera.analytics and now - last_analytics >= 0.5:
                last_analytics = now
                self._analyze(frame, now, tracker)
            if det.process(frame):
                self.motion_events += 1
                bbox, person = det.last_bbox, False
                if person_ok and now - last_person_check >= self.det_cfg.person_check_seconds:
                    last_person_check = now
                    box = self._person.detect(frame)
                    hits = hits + 1 if box is not None else 0
                    if box is not None and hits >= self.det_cfg.person_min_hits:
                        bbox, person = box, True
                self.alerts.motion(self.camera, frame, det.last_area_pct, bbox, person)
