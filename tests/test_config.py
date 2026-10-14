import stat

import pytest

from sentinel_guard.config import ConfigError, load_config
from sentinel_guard.camera import redact_url
from sentinel_guard.topics import generate_secure_topic, is_sentinel_topic, validate_topic

TOPIC = "sentinel_" + "a" * 32

BASE = f"""
[fcm]
project_id = "p"
service_account_file = "~/x.json"
topic = "{TOPIC}"
[[cameras]]
id = 1
name = "Q"
rtsp_url = "rtsp://u:p@h/x"
"""


def write(tmp_path, text, mode=0o600):
    p = tmp_path / "config.toml"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_topics():
    assert validate_topic("") is not None
    assert validate_topic("a b") is not None
    assert validate_topic("x" * 901) is not None
    assert validate_topic(TOPIC) is None
    t = generate_secure_topic()
    assert is_sentinel_topic(t) and validate_topic(t) is None


def test_load_ok(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.fcm.enabled and cfg.cameras[0].detection_url == "rtsp://u:p@h/x"


def test_rejects_open_permissions(tmp_path):
    with pytest.raises(ConfigError, match="chmod 600"):
        load_config(write(tmp_path, BASE, 0o644))


def test_rejects_bad_topic_and_dup_ids(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, BASE.replace(TOPIC, "bad topic")))
    with pytest.raises(ConfigError, match="duplicado"):
        load_config(write(tmp_path, BASE + '[[cameras]]\nid = 1\nname = "Z"\nrtsp_url = "rtsp://h/y"\n'))


def test_parse_and_overlay_geometry(tmp_path):
    from sentinel_guard.config import apply_geometry_overlay, parse_geometry
    g = parse_geometry({
        "include_zones": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
        "tripwires": [{"name": "portão", "line": [[0.5, 0], [0.5, 1]], "direction": "ab"}],
        "loiter_seconds": 20,
        "loiter_zone": [[0.1, 0.4], [0.9, 0.4], [0.9, 0.9], [0.1, 0.9]],
    }, 1)
    assert g["tripwires"][0]["direction"] == "ab" and g["loiter_seconds"] == 20
    with pytest.raises(ConfigError, match="line"):
        parse_geometry({"tripwires": [{"line": [[0, 0]]}]})
    cfg = load_config(write(tmp_path, BASE))
    cfg.data_dir = tmp_path
    (tmp_path / "geometry.json").write_text(
        '{"1":{"include_zones":[[[0,0],[1,0],[1,1],[0,1]]],"tripwires":'
        '[{"name":"p","line":[[0.2,0],[0.2,1]],"direction":"both"}]}}')
    apply_geometry_overlay(cfg)
    assert cfg.cameras[0].analytics and len(cfg.cameras[0].include_zones) == 1


def test_require_person_needs_filter(tmp_path):
    with pytest.raises(ConfigError, match="person_filter"):
        load_config(write(tmp_path, BASE + "[general]\nrequire_person = true\n"))


def test_secondary_preferred_and_redaction(tmp_path):
    cfg = load_config(write(tmp_path, BASE + 'rtsp_url_secondary = "rtsp://h/s"\n'))
    assert cfg.cameras[0].detection_url == "rtsp://h/s"
    r = redact_url("rtsp://u:secret@h:554/user=admin&password=secret&channel=1")
    assert "secret" not in r and "admin" not in r
    assert "secret" not in str(load_config(write(tmp_path, BASE.replace("u:p@", "u:secret@"))).cameras[0].to_json())
