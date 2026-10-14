#!/usr/bin/env bash
# Adiciona uma câmera IP ao config do notebook via SSH. A URL RTSP (com senha) é pedida no SEU
# terminal, sem eco, e vai por stdin do ssh: não aparece em histórico, args nem git.
# Uso: scripts/add_camera.sh [host_ssh=note]
set -euo pipefail
HOST="${1:-note}"
read -rp "Nome da câmera (ex.: Portão): " NAME
read -rsp "URL RTSP completa (rtsp://usuario:senha@ip:554/...): " URL; echo
read -rsp "URL RTSP secundária/substream (opcional, Enter para pular): " URL2; echo
[[ "$URL" == rtsp://* ]] || { echo "URL deve começar com rtsp://"; exit 1; }
printf '%s\n%s\n%s\n' "$NAME" "$URL" "$URL2" | ssh "$HOST" 'python3 -c "
import sys, os, tomllib, json
name, url, url2 = (sys.stdin.readline().rstrip(\"\n\") for _ in range(3))
p = os.path.expanduser(\"~/.config/ktsentinel-guard/config.toml\")
cams = tomllib.load(open(p, \"rb\")).get(\"cameras\", [])
nid = max([c[\"id\"] for c in cams] + [0]) + 1
blk = \"\n[[cameras]]\nid = %d\nname = %s\nrtsp_url = %s\n\" % (nid, json.dumps(name), json.dumps(url))
if url2: blk += \"rtsp_url_secondary = %s\n\" % json.dumps(url2)
blk += \"is_active = true\n\"
open(p, \"a\").write(blk)
os.chmod(p, 0o600)
print(\"câmera %d (%s) adicionada\" % (nid, name))
" && ~/.local/share/ktsentinel-guard/venv/bin/sentinel-guard check-config >/dev/null && ~/.local/share/ktsentinel-guard/venv/bin/sentinel-guard reload-config >/dev/null && echo "config válido e recarregado"'
