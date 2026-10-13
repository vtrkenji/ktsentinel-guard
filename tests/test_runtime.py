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
