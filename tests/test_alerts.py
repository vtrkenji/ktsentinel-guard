from datetime import datetime, time, timedelta

import numpy as np

from sentinel_guard.alerts import AlertManager
from sentinel_guard.camera import Camera
from sentinel_guard.config import Rules, ScheduleWindow

FRAME = np.zeros((48, 64, 3), np.uint8)
CAM = Camera(id=1, name="Quintal", rtsp_url="rtsp://h/x")


def make(tmp_path, **kw):
    t = [0.0]
    sent = []
    rules = Rules(**{"cooldown_seconds": 60, "min_event_seconds": 2, "event_gap_seconds": 5, **kw})
    am = AlertManager(tmp_path, rules, [lambda n, b: sent.append(b)], clock=lambda: t[0],
                      now=lambda: datetime(2026, 10, 13, 9, 0, 0).astimezone() + timedelta(seconds=t[0]))
    return am, t, sent


def burst(am, t, start, dur):
    for s in range(start, start + dur + 1):
        t[0] = s
        am.motion(CAM, FRAME, 3.0, (0.1, 0.1, 0.2, 0.2))


def wait_threads():
    import threading, time as _t
    for th in threading.enumerate():
        if th is not threading.current_thread() and th.daemon and th.name.startswith("Thread"):
            th.join(2)


def test_event_notifies_after_min_duration_and_logs_window(tmp_path):
    am, t, sent = make(tmp_path)
    burst(am, t, 0, 4)
    t[0] = 20
    am.tick()
    wait_threads()
    assert sent == ["Movimento detectado: Quintal às 09:00:00"]
    e = am.list_events()[0]
    assert e["start"].endswith("09:00:00-03:00") or "09:00:00" in e["start"]
    assert "09:00:04" in e["end"] and e["notified"] == 1
    assert e["dvrWindow"] == ["08:59:45", "09:00:19"]
    assert e["peakSnapshot"].endswith("_peak.jpg")


def test_short_blip_not_notified(tmp_path):
    am, t, sent = make(tmp_path)
    burst(am, t, 0, 1)
    t[0] = 10
    am.tick()
    wait_threads()
    assert sent == [] and am.list_events()[0]["reason"] == "curto"


def test_cooldown_and_hourly_cap(tmp_path):
    am, t, sent = make(tmp_path, max_notifications_per_hour=2)
    for start in (0, 30, 100, 200):
        burst(am, t, start, 3)
        t[0] = start + 20
        am.tick()
        wait_threads()
    reasons = [e["reason"] for e in reversed(am.list_events())]
    assert len(sent) == 2
    assert reasons == [None, "cooldown", None, "limite_hora"]


def test_observe_mode_only_logs(tmp_path):
    am, t, sent = make(tmp_path, notify=False)
    burst(am, t, 0, 4)
    t[0] = 20
    am.tick()
    wait_threads()
    assert sent == [] and am.list_events()[0]["reason"] == "somente_registro"


def test_schedule_window_crossing_midnight():
    w = ScheduleWindow(days=(0,), start=time(22, 0), end=time(6, 0))  # segunda 22h -> terça 6h
    mon = datetime(2026, 10, 12, 23, 0)
    tue = datetime(2026, 10, 13, 5, 0)
    assert w.active(mon) and w.active(tue)
    assert not w.active(datetime(2026, 10, 13, 7, 0)) and not w.active(datetime(2026, 10, 13, 23, 0))


def _run_event(am, t, person_at=None, seconds=6):
    import numpy as np
    from sentinel_guard.camera import Camera
    cam = Camera(id=9, name="Garagem", rtsp_url="rtsp://h/x")
    for s in range(seconds):
        t[0] = s
        am.motion(cam, np.zeros((48, 64, 3), np.uint8), 3.0, (0.1, 0.1, 0.2, 0.2), person=person_at is not None and s >= person_at)
    t[0] = seconds + 10
    am.tick()


def test_require_person_blocks_motion_only(tmp_path):
    sent = []
    t = [0.0]
    am = AlertManager(tmp_path, Rules(require_person=True), [lambda n, b: sent.append(b)], clock=lambda: t[0])
    _run_event(am, t)
    assert not sent
    ev = am.list_events()[0]
    assert ev["reason"] == "sem_pessoa" and ev["person"] is False


def test_require_person_notifies_when_person_seen_late(tmp_path):
    import time
    sent = []
    t = [0.0]
    am = AlertManager(tmp_path, Rules(require_person=True), [lambda n, b: sent.append(b)], clock=lambda: t[0])
    _run_event(am, t, person_at=4)
    time.sleep(0.3)
    assert len(sent) == 1 and sent[0].startswith("Pessoa detectada")
    assert am.list_events()[0]["person"] is True


def test_prune_remove_antigos_e_respeita_teto(tmp_path):
    import os
    from sentinel_guard.alerts import AlertManager
    from sentinel_guard.config import Rules
    am = AlertManager(tmp_path, Rules(keep_days=1, max_snapshots_mb=0), [])
    snaps = tmp_path / "snapshots"
    old, new = snaps / "old.jpg", snaps / "new.jpg"
    old.write_bytes(b"x"); new.write_bytes(b"x")
    os.utime(old, (1, 1))
    assert am.prune() == 1 and not old.exists() and new.exists()
    am.rules.keep_days = 0
    am.rules.max_snapshots_mb = 1
    (snaps / "big.jpg").write_bytes(b"x" * 2 * 1024 * 1024)
    am.prune()
    assert not (snaps / "big.jpg").exists() or not new.exists()


def test_behavior_only_registra_movimento_com_pessoa(tmp_path):
    am, t, sent = make(tmp_path, behavior_only=True)
    for s in range(0, 6):
        t[0] = s
        am.motion(CAM, FRAME, 3.0, (0.1, 0.1, 0.2, 0.2), True)
    t[0] = 20
    am.tick()
    wait_threads()
    assert sent == []
    e = am.list_events()[0]
    assert e["reason"] == "so_registro" and not e["notified"]
    assert am.list_events(only_notified=True) == []


def test_behavior_only_ainda_notifica_loiter(tmp_path):
    am, t, sent = make(tmp_path, behavior_only=True)
    am.rule_event(CAM, FRAME, (0.1, 0.1, 0.2, 0.2), "loiter", "Pessoa parada")
    wait_threads()
    assert len(sent) == 1
    assert len(am.list_events(only_notified=True)) == 1
