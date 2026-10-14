# ktsentinel-guard
[![CI](https://github.com/vtrkenji/ktsentinel-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/vtrkenji/ktsentinel-guard/actions/workflows/ci.yml)

Detecção de movimento em câmeras RTSP com alertas FCM (Linux)

Daemon `sentinel-guard`: lê o RTSP das câmeras (stream secundário quando existir), detecta
movimento (OpenCV MOG2), confirma pessoa (MobileNet-SSD) e, no perfil Segurança, só envia push FCM
em tripwire ou loitering. Sem o modelo de pessoa **não há aviso** (fail-closed). Tópico
`sentinel_<32 hex>`, título `kTsentinel - Casa` (mesmo formato do firmware ESP32).

Zonas (área de scan, ignore, tripwire, loiter) desenham-se no **notebook**, numa foto JPEG — não no
celular e não em vídeo ao vivo, para não gastar CPU extra. O app Flutter só opera o alarme depois.

## Instalação (notebook)
Atalho: baixe `ktsentinel-guard-linux.tar.gz` em Releases, extraia e rode `./scripts/install.sh`. Manual:
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
Movimento vira um *evento* (início/fim no horário local, igual ao relógio do DVR). Com
`require_person` + `behavior_only` (padrão no `config.example.toml`) o push só sai em tripwire/loiter
com pessoa; o resto fica no histórico com o motivo (`sem_pessoa`, `so_registro`, cooldown, limite).
`notify = false` = calibrar (sem push). Aba **Zonas** na página web, ou `test-camera`, para desenhar.

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
`Authorization: Bearer <token>`. Rotas: `GET /v1/status` (inclui `warnings`, estado das câmeras, modelo de
pessoa), `GET /v1/cameras`, `POST /v1/arm`, `POST /v1/disarm`, `POST /v1/test-notification`,
`GET /v1/events?limit=&since=`, `GET /v1/snapshot?id=N&kind=peak|start`, `GET /v1/preview?id=N` (um JPEG),
`PUT /v1/cameras/{id}/geometry`.

## Filtro de pessoa

`scripts/download_models.sh` baixa o MobileNet-SSD (~23 MB) para `~/.local/share/ktsentinel-guard/models`.
Com `[detection] person_filter = true`, o modelo confirma pessoa quando há movimento. Se o arquivo do
modelo faltar, o daemon **não desliga o filtro**: nenhum push até `download_models.sh`.
`require_person = true` ignora carros/animais/sombras (`reason = sem_pessoa`).

## Tripwire e loitering

Por câmera, com `person_filter` ligado (veja `packaging/config.example.toml`). Roda o detector de pessoas
~2x/s, rastreia cada pessoa (pé = base da caixa) e gera eventos `kind = tripwire` (cruzou a linha, na direção
configurada) ou `kind = loiter` (ficou `loiter_seconds` na cena/zona). Cada tipo tem cooldown próprio e conta
no limite de notificações por hora.

## Ver snapshots e histórico pelo navegador

Com `[api]` habilitado, abra `http://<ip-do-notebook>:8765/` e informe o token (`~/.config/ktsentinel-guard/api-token`).
**Eventos**: histórico e snapshot. **Zonas**: uma foto do stream (não live) para desenhar scan, ignore,
tripwire e loiter; grava `geometry.json` no data dir (sobrepõe o TOML). Armar/desarmar e testar FCM no
cabeçalho. O token fica só na sessão do navegador. Isso no notebook gasta bem menos que um editor no
celular (sem segundo decoder de RTSP).

Para acessar de fora de casa sem expor nada na internet: instale o Tailscale no notebook (`sudo tailscale up`)
e no celular, e troque `bind` para o IP Tailscale do notebook (`tailscale ip -4`) ou `0.0.0.0` com firewall.
As fotos continuam só no notebook; nada é enviado a serviços de nuvem.

### Acesso de fora via DuckDNS (HTTPS)

`scripts/setup_https.sh [dominio] [porta=8443]` instala o Caddy (sem root) no notebook, obtém o certificado
Let's Encrypt por DNS-01 e faz proxy para a API. O token do DuckDNS é pedido no seu terminal e fica só em
`~/.config/ktsentinel-guard/duckdns.env` (600). Depois encaminhe no roteador a porta externa 8443 para o notebook
e abra `https://<dominio>:8443/`. A API bloqueia por 5 min o IP que errar o token 5 vezes (o IP real vem do
cabeçalho `X-Forwarded-For` do proxy local). Observação: o token do DuckDNS vale para todos os seus domínios.
