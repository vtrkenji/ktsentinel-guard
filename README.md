# ktsentinel-guard
Detecção de movimento em câmeras RTSP com alertas FCM (Linux)

Daemon `sentinel-guard` (etapa 1): lê o RTSP das câmeras (stream secundário quando existir), detecta
movimento (OpenCV MOG2), salva snapshot, registra histórico (SQLite) e envia push FCM HTTP v1 para o
tópico `sentinel_<32 hex>` com título `kTsentinel - Casa` (mesmo formato do firmware ESP32).

## Instalação (notebook)
```
python3 -m venv --system-site-packages ~/.local/share/ktsentinel-guard/venv   # OpenCV do sistema
~/.local/share/ktsentinel-guard/venv/bin/pip install .
mkdir -p ~/.config/ktsentinel-guard && chmod 700 ~/.config/ktsentinel-guard
cp packaging/config.example.toml ~/.config/ktsentinel-guard/config.toml && chmod 600 ~/.config/ktsentinel-guard/config.toml
# chave de service account do Firebase: copie para ~/.config/ktsentinel-guard/fcm-service-account.json (chmod 600)
cp packaging/sentinel-guard.service ~/.config/systemd/user/ && systemctl --user enable --now sentinel-guard
```
Sem `python3-venv` (Debian/Mint): `python3 -m venv --system-site-packages --without-pip VENV` e
`pip --python VENV/bin/python install .`.

## Comandos
`sentinel-guard [--config PATH] <cmd>`: `run`, `check-config`, `gen-topic`, `test-fcm [--dry-run]`,
`test-camera [id]`, `events [--since]`, `status`, `arm`, `disarm`, `list-cameras`, `alerts`, `test-notification`, `reload-config`.

Validar FCM: `check-config` → `test-fcm --dry-run` (validate_only, nada é entregue) → `test-fcm` (chega no app).

## Regras e eventos
Movimento vira um *evento* (início/fim no horário local, igual ao relógio do DVR). Só notifica se durar
`min_event_seconds`, fora do `cooldown_seconds` e abaixo de `max_notifications_per_hour`; o resto fica
registrado com o motivo. `sentinel-guard events` lista os eventos com a janela para assistir no DVR e o
snapshot do pico (com a caixa do movimento). `notify = false` = modo observação. `test-camera` desenha as zonas.

## Socket
`$XDG_RUNTIME_DIR/ktsentinel-guard.sock` (600), JSON por linha: `{"id":1,"cmd":"status","args":{}}` →
`{"id":1,"ok":true,"result":{...}}`. Comandos: status, arm, disarm, list_cameras, 
test_notification, reload_config, list_events{limit,since}.

## Testes
`pip install -e .[dev] opencv-python-headless && pytest`. Integração com stream falso:
`scripts/fake_rtsp.sh` (precisa de mediamtx + ffmpeg).

**Segredos nunca vão para o git**: config.toml, chave FCM e credenciais RTSP ficam só em `~/.config/ktsentinel-guard/`.
