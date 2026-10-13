from datetime import datetime

import pytest

from sentinel_guard.config import Config
from sentinel_guard.daemon import Daemon


def make(tmp_path):
    d = Daemon(tmp_path / "c.toml")
    d.cfg = Config(data_dir=tmp_path)
    d.schedule = d._load_schedule()
    return d


def test_set_get_e_persistencia(tmp_path):
    d = make(tmp_path)
    wins = [{"days": [5, 6], "start": "11:30", "end": "05:00"},
            {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}]
    res = d.cmd_set_schedule(wins)
    assert res["enabled"] and res["windows"][0]["start"] == "11:30"
    d2 = make(tmp_path)
    assert d2.cmd_get_schedule()["windows"] == wins
    assert d2.schedule[0].active(datetime(2026, 10, 17, 12, 0))  # sábado
    assert d2.schedule[0].active(datetime(2026, 10, 18, 3, 0))   # madrugada de domingo


def test_desativar_mantem_janelas_e_arma_sempre(tmp_path):
    d = make(tmp_path)
    d.cmd_set_schedule([{"days": [0], "start": "01:00", "end": "02:00"}])
    d.cmd_set_schedule([{"days": [0], "start": "01:00", "end": "02:00"}], enabled=False)
    assert d.cmd_get_schedule()["enabled"] is False
    assert d.is_armed()
    assert len(d.cmd_get_schedule()["windows"]) == 1


@pytest.mark.parametrize("w", [{"days": [7], "start": "01:00", "end": "02:00"},
                               {"days": [], "start": "01:00", "end": "02:00"},
                               {"days": [0], "start": "25:00", "end": "02:00"},
                               {"days": [0], "start": "xx", "end": "02:00"}])
def test_janela_invalida(tmp_path, w):
    with pytest.raises(ValueError):
        make(tmp_path).cmd_set_schedule([w])
