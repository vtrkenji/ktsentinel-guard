from sentinel_guard.hud import describe


def test_manual_fora_da_agenda():
    view = describe(
        {"armed": True, "scheduleActive": True, "manualOverride": True, "inWindow": False,
         "cameras": [{"name": "Quintal", "state": "online"}], "warnings": []},
        api_on=True,
    )
    assert view["headline"] == "ON"
    assert view["tone"] == "on"
    assert view["source"] == "ligado no manual"
    assert view["cameras"] == "Quintal online"
    assert "pode falar" in view["api"]


def test_agenda_e_pausa():
    view = describe(
        {"armed": True, "scheduleActive": False, "manualOverride": None, "inWindow": False,
         "cameras": [], "warnings": ["armado, porém nenhuma câmera online"]},
    )
    assert view["headline"] == "PAUSA"
    assert view["source"] == "fora da agenda"
    assert "câmera" in view["warnings"]


def test_daemon_antigo_sem_agenda():
    view = describe(
        {"armed": True, "scheduleActive": True, "cameras": [{"name": "Quintal", "state": "offline"}]},
        schedule={"enabled": False, "windows": []},
        api_on=False,
    )
    assert view["headline"] == "ON"
    assert view["source"] == "sem agenda, ligado direto"
    assert view["api"] == "API do celular desligada"


def test_daemon_antigo_dentro_da_agenda():
    view = describe(
        {"armed": True, "scheduleActive": True, "cameras": []},
        schedule={"enabled": True, "windows": [{"days": [0], "start": "08:00", "end": "18:00"}]},
    )
    assert view["source"] == "dentro da agenda"


def test_daemon_parado():
    view = describe(None)
    assert view["headline"] == "PARADO"
    assert view["tone"] == "off"
