import logging
import os
import threading
import time

import cv2

from .camera import Camera, redact_url
from .config import DetectionConfig
from .detector import MotionDetector

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

    def stop(self) -> None:
        self._stop_evt.set()

    def status(self) -> dict:
        age = None if self.last_frame_at is None else round(time.monotonic() - self.last_frame_at, 1)
        return {**self.camera.to_json(), "state": self.state, "lastFrameAgeSeconds": age,
                "lastError": self.last_error, "reconnects": self.reconnects, "motionEvents": self.motion_events,
                "usingSecondary": bool(self.camera.rtsp_url_secondary)}

    def _detector(self) -> MotionDetector:
        c, d = self.camera, self.det_cfg
        return MotionDetector(
            sensitivity=c.sensitivity or d.sensitivity,
            min_area_pct=c.min_area_pct if c.min_area_pct is not None else d.min_area_pct,
            min_consecutive_frames=c.min_consecutive_frames or d.min_consecutive_frames,
            history=d.history, warmup_frames=int(d.warmup_seconds * d.fps))

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
        interval = 1.0 / self.det_cfg.fps
        next_due = 0.0
        was_armed = False
        while not self._stop_evt.is_set():
            if not cap.grab():
                return
            now = time.monotonic()
            self.last_frame_at = now
            if now < next_due:
                continue
            armed = self.is_armed()
            if not armed:
                was_armed = False
                next_due = now + 0.5
                continue
            if not was_armed:
                det.reset()
                was_armed = True
            ok, frame = cap.retrieve()
            next_due = now + interval
            if not ok:
                return
            if det.process(frame):
                self.motion_events += 1
                self.alerts.trigger(self.camera, frame)
