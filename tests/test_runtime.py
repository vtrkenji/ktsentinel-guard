import json
import time

import numpy as np

from sentinel_guard.alerts import AlertManager
from sentinel_guard.config import Rules
from sentinel_guard.camera import Camera
from sentinel_guard.capture import CameraWorker
from sentinel_guard.config import DetectionConfig, FcmConfig
from sentinel_guard.fcm import FcmError, FcmSender, build_message
from sentinel_guard.server import SocketServer, request
import pytest

FRAME = np.zeros((48, 64, 3), np.uint8)
CAM = Camera(id=1, name="Quintal", rtsp_url="rtsp://h/x")


class FakeResp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b, self.text = code, body or {"name": "projects/p/messages/1"}, "err"

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return self.resp


def make_sender(resp):
    s = FcmSender(FcmConfig("proj", "x.json", "sentinel_" + "b" * 32), session=FakeSession(resp))
    s._token = lambda: "tok"
    return s


def test_fcm_payload_matches_firmware_format():
    s = make_sender(FakeResp())
    s.send("Movimento detectado: Q")
    url, payload, headers = s._session.calls[0]
    assert url == "https://fcm.googleapis.com/v1/projects/proj/messages:send"
    assert payload["message"]["topic"] == "sentinel_" + "b" * 32
    assert payload["message"]["notification"]["title"] == "kTsentinel - Casa"
    assert headers["Authorization"] == "Bearer tok" and "validate_only" not in payload
    s.send("x", validate_only=True)
    assert s._session.calls[1][1]["validate_only"] is True


def test_fcm_error():
    with pytest.raises(FcmError, match="403"):
        make_sender(FakeResp(403)).send("x")


class FakeDaemon:
    def cmd_status(self):
        return {"armed": True}

    def cmd_alerts(self, limit=1):
        return [limit]


def test_socket_protocol(tmp_path):
    p = tmp_path / "s.sock"
    srv = SocketServer(p, FakeDaemon())
    srv.start()
    try:
        assert oct(p.stat().st_mode & 0o777) == "0o600"
        assert request(p, "status") == {"id": 1, "ok": True, "result": {"armed": True}}
        assert request(p, "alerts", {"limit": 5})["result"] == [5]
        assert request(p, "nope")["ok"] is False
    finally:
        srv.close()


class FlakyCap:
    opens = 0

    def __init__(self, url, api=None):
        FlakyCap.opens += 1
        self.n = 0
        self.ok = FlakyCap.opens > 1

    def isOpened(self):
        return self.ok

    def grab(self):
        self.n += 1
        time.sleep(0.01)
        return self.n < 5

    def retrieve(self):
        return True, FRAME

    def release(self):
        pass


class SteadyCap:
    def __init__(self, url, api=None):
        self.n = 0

    def isOpened(self):
        return True

    def grab(self):
        self.n += 1
        time.sleep(0.01)
        return self.n < 400

    def retrieve(self):
        return True, FRAME

    def release(self):
        pass


def test_worker_reconnects(tmp_path):
    am = AlertManager(tmp_path, Rules(), [])
    w = CameraWorker(CAM, DetectionConfig(fps=50), am, lambda: True, capture_factory=FlakyCap)
    w.start()
    deadline = time.time() + 8
    while w.reconnects < 3 and time.time() < deadline:
        time.sleep(0.1)
    w.stop()
    w.join(3)
    assert w.reconnects >= 3 and FlakyCap.opens >= 3


def test_preview_one_frame_while_disarmed(tmp_path):
    am = AlertManager(tmp_path, Rules(), [])
    w = CameraWorker(CAM, DetectionConfig(fps=50), am, lambda: False, capture_factory=SteadyCap)
    w.start()
    jpeg = None
    deadline = time.time() + 4
    while time.time() < deadline:
        jpeg = w.preview_jpeg(timeout=0.8)
        if jpeg:
            break
    w.stop()
    w.join(3)
    assert jpeg and jpeg[:2] == b"\xff\xd8"


def test_missing_person_model_is_degraded_fail_closed(tmp_path):
    sent = []
    am = AlertManager(tmp_path, Rules(require_person=True, min_event_seconds=0.01, notify=True),
                      [lambda n, b: sent.append(b)])
    det = DetectionConfig(person_filter=True, person_model_dir=tmp_path / "no-model",
                          fps=50, warmup_seconds=0)
    w = CameraWorker(CAM, det, am, lambda: True, capture_factory=SteadyCap)
    w.start()
    deadline = time.time() + 4
    while w.person_model == "pending" and time.time() < deadline:
        time.sleep(0.05)
    assert w.person_model == "missing"
    deadline = time.time() + 3
    while w.state == "connecting" and time.time() < deadline:
        time.sleep(0.05)
    assert w.state in ("degraded", "online")
    w.stop()
    w.join(3)
    assert sent == []


def test_fail_closed_forces_require_person(tmp_path):
    from sentinel_guard.daemon import Daemon
    from sentinel_guard.config import DetectionConfig, Rules
    d = Daemon(tmp_path / "missing.toml")
    d.cfg = type("C", (), {})()
    d.cfg.detection = DetectionConfig(person_filter=True, person_model_dir=tmp_path / "nope")
    d.cfg.rules = Rules(require_person=False, notify=True)
    d._enforce_fail_closed()
    assert d.cfg.rules.require_person is True


def test_set_geometry_writes_overlay(tmp_path):
    from sentinel_guard.daemon import Daemon
    from sentinel_guard.config import load_config
    p = tmp_path / "config.toml"
    p.write_text(
        "[detection]\nperson_filter = true\n"
        "[fcm]\nproject_id = \"p\"\nservice_account_file = \"~/x.json\"\n"
        "topic = \"sentinel_" + "a" * 32 + "\"\n"
        "[[cameras]]\nid = 1\nname = \"Q\"\nrtsp_url = \"rtsp://h/x\"\n"
    )
    p.chmod(0o600)
    d = Daemon(p)
    d.cfg = load_config(p)
    d.cfg.data_dir = tmp_path / "data"
    d.cfg.data_dir.mkdir()
    d.cfg.detection.person_filter = True
    d.workers, d.alerts = [], None
    out = d.cmd_set_geometry(1, {
        "include_zones": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        "tripwires": [{"name": "portão", "line": [[0.4, 0], [0.4, 1]], "direction": "both"}],
        "loiter_seconds": 15,
    })
    assert out["tripwires"] and (d.cfg.data_dir / "geometry.json").is_file()
    assert d.cfg.cameras[0].loiter_seconds == 15
