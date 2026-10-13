import time

import numpy as np
import pytest

from sentinel_guard.alerts import AlertManager
from sentinel_guard.camera import Camera
from sentinel_guard.config import ConfigError, Rules, load_config
from sentinel_guard.tracker import Tracker, crossing, point_in_polygon

A, B = (0.5, 0.0), (0.5, 1.0)  # linha vertical no meio


def test_crossing_directions():
    assert crossing(A, B, (0.4, 0.5), (0.6, 0.5)) == "ba"
    assert crossing(A, B, (0.6, 0.5), (0.4, 0.5)) == "ab"
    assert crossing(A, B, (0.1, 0.5), (0.4, 0.5)) is None
    assert crossing(A, (0.5, 0.2), (0.4, 0.8), (0.6, 0.8)) is None  # fora do segmento


def test_tracker_follows_and_creates():
    tr = Tracker()
    t1 = tr.update([(0.1, 0.1, 0.1, 0.4)], 0)[0]
    t2 = tr.update([(0.12, 0.1, 0.1, 0.4)], 1)[0]
    assert t1 is t2 and t2.prev_foot is not None
    t3 = tr.update([(0.8, 0.1, 0.1, 0.4)], 2)[0]
    assert t3.id != t1.id
    tr.update([], 20)
    assert tr.tracks == []


def test_polygon():
    sq = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert point_in_polygon((0.5, 0.5), sq) and not point_in_polygon((1.5, 0.5), sq)


def test_rule_event_notifies_with_own_cooldown(tmp_path):
    sent = []
    am = AlertManager(tmp_path, Rules(), [lambda n, b: sent.append(b)])
    cam = Camera(id=1, name="Portão", rtsp_url="rtsp://h/x")
    frame = np.zeros((48, 64, 3), np.uint8)
    am.rule_event(cam, frame, (0.1, 0.1, 0.2, 0.5), "tripwire", "Pessoa cruzou linha")
    am.rule_event(cam, frame, None, "loiter", "Pessoa parada/rondando")
    am.rule_event(cam, frame, None, "tripwire", "Pessoa cruzou linha")  # cooldown
    time.sleep(0.3)
    assert len(sent) == 2
    kinds = sorted(e["kind"] for e in am.list_events())
    assert kinds == ["loiter", "tripwire", "tripwire"]


def test_config_tripwire(tmp_path):
    p = tmp_path / "c.toml"
    base = ('[fcm]\nproject_id="p"\ntopic="sentinel_vitor_01"\nservice_account_file="x"\n'
            '[[cameras]]\nid=1\nname="a"\nrtsp_url="rtsp://h/x"\nloiter_seconds=20\n'
            '[[cameras.tripwires]]\nname="portão"\nline=[[0.5,0],[0.5,1]]\ndirection="ab"\n')
    p.write_text(base)
    with pytest.raises(ConfigError, match="person_filter"):
        load_config(p, require_private=False)
    p.write_text("[detection]\nperson_filter=true\n" + base)
    cam = load_config(p, require_private=False).cameras[0]
    assert cam.analytics and cam.tripwires[0]["direction"] == "ab" and cam.loiter_seconds == 20
