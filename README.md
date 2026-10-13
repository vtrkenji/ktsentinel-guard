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
`test-camera [id]`, `events [--since]`, `status`, `arm`, `disarm`, `list-cameras`, `test-notification`, `reload-config`.

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

## API HTTP (controle remoto, opcional)

Desligada por padrão. Para ativar, adicione ao config:

```toml
[api]
enabled = true
bind = "127.0.0.1"   # use o IP do Tailscale/LAN; nunca exponha na internet
port = 8765
```

Gere o token com `sentinel-guard gen-token` (grava `~/.config/ktsentinel-guard/api-token`, perm 600) e envie
`Authorization: Bearer <token>`. Rotas: `GET /v1/status`, `GET /v1/cameras`, `POST /v1/arm`, `POST /v1/disarm`,
`GET /v1/events?limit=&since=`, `GET /v1/snapshot?id=N&kind=peak|start`.

## Filtro de pessoa

`scripts/download_models.sh` baixa o MobileNet-SSD (~23 MB) para `~/.local/share/ktsentinel-guard/models`.
Com `[detection] person_filter = true`, quando o MOG2 detecta movimento o modelo roda no máx. 1x/s e marca o
evento (`person` no histórico, bbox da pessoa no snapshot). Com `[general] require_person = true` só pessoas
notificam; movimento sem pessoa fica no histórico com `reason = "sem_pessoa"`.

## Tripwire e loitering

Por câmera, com `person_filter` ligado (veja `packaging/config.example.toml`). Roda o detector de pessoas
~2x/s, rastreia cada pessoa (pé = base da caixa) e gera eventos `kind = tripwire` (cruzou a linha, na direção
configurada) ou `kind = loiter` (ficou `loiter_seconds` na cena/zona). Cada tipo tem cooldown próprio e conta
no limite de notificações por hora.

## Ver snapshots e histórico pelo navegador

Com `[api]` habilitado, abra `http://<ip-do-notebook>:8765/` e informe o token (`~/.config/ktsentinel-guard/api-token`).
A página lista os eventos com o snapshot de pico (nomeado por data/hora), mostra a janela para achar no DVR e
tem botões de armar/desarmar. O token fica só na sessão do navegador.

Para acessar de fora de casa sem expor nada na internet: instale o Tailscale no notebook (`sudo tailscale up`)
e no celular, e troque `bind` para o IP Tailscale do notebook (`tailscale ip -4`) ou `0.0.0.0` com firewall.
As fotos continuam só no notebook; nada é enviado a serviços de nuvem.

### Acesso de fora via DuckDNS (HTTPS)

`scripts/setup_https.sh [dominio] [porta=8443]` instala o Caddy (sem root) no notebook, obtém o certificado
Let's Encrypt por DNS-01 e faz proxy para a API. O token do DuckDNS é pedido no seu terminal e fica só em
`~/.config/ktsentinel-guard/duckdns.env` (600). Depois encaminhe no roteador a porta externa 8443 para o notebook
e abra `https://<dominio>:8443/`. A API bloqueia por 5 min o IP que errar o token 5 vezes (o IP real vem do
cabeçalho `X-Forwarded-For` do proxy local). Observação: o token do DuckDNS vale para todos os seus domínios.
