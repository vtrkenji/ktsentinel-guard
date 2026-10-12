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


def test_secondary_preferred_and_redaction(tmp_path):
    cfg = load_config(write(tmp_path, BASE + 'rtsp_url_secondary = "rtsp://h/s"\n'))
    assert cfg.cameras[0].detection_url == "rtsp://h/s"
    r = redact_url("rtsp://u:secret@h:554/user=admin&password=secret&channel=1")
    assert "secret" not in r and "admin" not in r
    assert "secret" not in str(load_config(write(tmp_path, BASE.replace("u:p@", "u:secret@"))).cameras[0].to_json())
