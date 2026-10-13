#!/usr/bin/env bash
# Publica a página/API do Guard em HTTPS via DuckDNS (Caddy, sem root, certificado por DNS-01).
# O token do DuckDNS é pedido no SEU terminal e gravado só no notebook (perm. 600).
# Depois: no roteador, encaminhe a porta externa (padrão 8443) para <ip-notebook>:8443.
# Uso: scripts/setup_https.sh [dominio] [porta=8443] [host_ssh=note]
set -euo pipefail
DOMAIN="${1:-ktsentinelhubapp.duckdns.org}"
PORT="${2:-8443}"
HOST="${3:-note}"
read -rsp "Token do DuckDNS (não aparece na tela): " TOKEN; echo
[[ -n "$TOKEN" ]] || { echo "token vazio"; exit 1; }

printf 'DUCKDNS_TOKEN=%s\n' "$TOKEN" | ssh "$HOST" 'umask 077; mkdir -p ~/.config/ktsentinel-guard; cat > ~/.config/ktsentinel-guard/duckdns.env; chmod 600 ~/.config/ktsentinel-guard/duckdns.env'
unset TOKEN

ssh "$HOST" bash -s -- "$DOMAIN" "$PORT" <<'REMOTE'
set -euo pipefail
DOMAIN="$1"; PORT="$2"
BIN=~/.local/share/ktsentinel-guard/bin; mkdir -p "$BIN" ~/.config/systemd/user
if [ ! -x "$BIN/caddy" ]; then
  curl -fsSL -o "$BIN/caddy" "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/duckdns"
  chmod +x "$BIN/caddy"
fi
"$BIN/caddy" list-modules | grep -q dns.providers.duckdns
IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.*src \([0-9.]*\).*/\1/p')
cat > ~/.config/ktsentinel-guard/Caddyfile <<CADDY
{
	admin off
}
$DOMAIN:$PORT {
	tls {
		dns duckdns {env.DUCKDNS_TOKEN}
	}
	reverse_proxy $IP:8765
}
CADDY
cat > ~/.config/systemd/user/ktsentinel-caddy.service <<UNIT
[Unit]
Description=kTsentinel Guard HTTPS (Caddy)
After=network-online.target

[Service]
EnvironmentFile=%h/.config/ktsentinel-guard/duckdns.env
Environment=XDG_DATA_HOME=%h/.local/share/ktsentinel-guard
ExecStart=%h/.local/share/ktsentinel-guard/bin/caddy run --config %h/.config/ktsentinel-guard/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now ktsentinel-caddy
sleep 25
systemctl --user is-active ktsentinel-caddy
journalctl --user -u ktsentinel-caddy --no-pager -n 8 | grep -iE "obtain|certificate|error|serving" | cut -c1-200 | tail -4
REMOTE
echo "Pronto. Encaminhe no roteador: porta externa $PORT -> notebook:$PORT, e abra https://$DOMAIN:$PORT/"
