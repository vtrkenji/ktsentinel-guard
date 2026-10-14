import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import numpy as np

from sentinel_guard.alerts import AlertManager
from sentinel_guard.api import ApiServer
from sentinel_guard.camera import Camera
from sentinel_guard.config import ApiConfig, Rules

TOKEN = "t" * 40
CAM = Camera(id=1, name="Quintal", rtsp_url="rtsp://h/x")


class FakeDaemon:
    def __init__(self, alerts):
        self.alerts = alerts
        self.armed = True

    def cmd_status(self):
        return {"armed": self.armed}

    def cmd_arm(self):
        self.armed = True
        return {"armed": True}

    def cmd_disarm(self):
        self.armed = False
        return {"armed": False}

    def cmd_list_cameras(self):
        return []

    def cmd_list_events(self, limit=50, since=None, only_notified=False):
        return self.alerts.list_events(limit, since)

    def cmd_test_notification(self):
        return {"sent": 1}

    def cmd_preview(self, camera_id):
        return b"\xff\xd8\xffpreview"

    def cmd_set_geometry(self, camera_id, values):
        self.geometry = (int(camera_id), values)
        return {"id": int(camera_id), "ok": True}


def call(base, path, method="GET", token=TOKEN):
    req = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_api(tmp_path):
    tf = tmp_path / "token"
    tf.write_text(TOKEN)
    tf.chmod(0o600)
    t = [0.0]
    am = AlertManager(tmp_path / "d", Rules(notify=False), [], clock=lambda: t[0],
                      now=lambda: datetime(2026, 10, 13, 9, 0, 0).astimezone() + timedelta(seconds=t[0]))
    for s in range(4):
        t[0] = s
        am.motion(CAM, np.zeros((48, 64, 3), np.uint8), 3.0, (0.1, 0.1, 0.2, 0.2))
    t[0] = 20
    am.tick()
    d = FakeDaemon(am)
    api = ApiServer(ApiConfig(enabled=True, port=0, token_file=tf), d)
    api.start()
    base = f"http://127.0.0.1:{api.address[1]}"
    try:
        assert call(base, "/v1/status", token=None)[0] == 401
        assert call(base, "/v1/status", token="x" * 40)[0] == 401
        code, _, body = call(base, "/v1/status")
        assert code == 200 and json.loads(body)["result"] == {"armed": True}
        assert json.loads(call(base, "/v1/disarm", "POST")[2])["result"] == {"armed": False} and not d.armed
        assert call(base, "/v1/disarm")[0] == 404  # método errado
        ev = json.loads(call(base, "/v1/events")[2])["result"]
        assert len(ev) == 1 and ev[0]["dvrWindow"]
        code, ctype, img = call(base, f"/v1/snapshot?id={ev[0]['id']}")
        assert code == 200 and ctype == "image/jpeg" and img[:2] == b"\xff\xd8"
        assert call(base, "/v1/snapshot?id=999")[0] == 404
        assert call(base, "/v1/events?limit=abc")[0] == 400
        code, ctype, img = call(base, "/v1/preview?id=1")
        assert code == 200 and ctype == "image/jpeg" and img.startswith(b"\xff\xd8")
        req = urllib.request.Request(
            base + "/v1/cameras/1/geometry", method="PUT",
            data=json.dumps({"tripwires": [{"name": "p", "line": [[0.5, 0], [0.5, 1]]}]}).encode(),
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
        assert d.geometry[0] == 1
        assert json.loads(call(base, "/v1/test-notification", "POST")[2])["result"]["sent"] == 1
    finally:
        api.close()


def test_snapshot_path_rejects_outside_dir(tmp_path):
    am = AlertManager(tmp_path, Rules(), [])
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"x")
    am._db.execute("INSERT INTO events (camera_id, camera_name, start_ts, peak_snapshot) VALUES (1,'c','2026-01-01T00:00:00',?)",
                   (str(outside),))
    am._db.commit()
    assert am.snapshot_path(1) is None


def test_web_page_is_public(tmp_path):
    tf = tmp_path / "token"
    tf.write_text(TOKEN)
    tf.chmod(0o600)
    am = AlertManager(tmp_path / "d", Rules(notify=False), [])
    api = ApiServer(ApiConfig(enabled=True, port=0, token_file=tf), FakeDaemon(am))
    api.start()
    try:
        code, ctype, body = call(f"http://127.0.0.1:{api.address[1]}", "/", token=None)
        assert code == 200 and ctype.startswith("text/html") and b"kTsentinel Guard" in body
        assert b"Zonas" in body and TOKEN.encode() not in body
    finally:
        api.close()


def test_lockout_after_failures(tmp_path):
    tf = tmp_path / "token"
    tf.write_text(TOKEN)
    tf.chmod(0o600)
    am = AlertManager(tmp_path / "d", Rules(notify=False), [])
    api = ApiServer(ApiConfig(enabled=True, port=0, token_file=tf), FakeDaemon(am))
    api.start()
    base = f"http://127.0.0.1:{api.address[1]}"
    try:
        codes = [call(base, "/v1/status", token="x" * 40)[0] for _ in range(5)]
        assert codes == [401] * 5
        assert call(base, "/v1/status", token="x" * 40)[0] == 429
        assert call(base, "/v1/status")[0] == 429  # bloqueado mesmo com token certo
    finally:
        api.close()


def test_lockout_uses_forwarded_ip_from_local_proxy(tmp_path):
    tf = tmp_path / "token"
    tf.write_text(TOKEN)
    tf.chmod(0o600)
    am = AlertManager(tmp_path / "d", Rules(notify=False), [])
    api = ApiServer(ApiConfig(enabled=True, port=0, token_file=tf), FakeDaemon(am))
    api.start()
    base = f"http://127.0.0.1:{api.address[1]}"

    def hit(ip, token):
        req = urllib.request.Request(base + "/v1/status", headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": ip})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        for _ in range(5):
            assert hit("203.0.113.9", "x" * 40) == 401
        assert hit("203.0.113.9", TOKEN) == 429
        assert hit("198.51.100.1", TOKEN) == 200  # outro IP externo não é afetado
    finally:
        api.close()
