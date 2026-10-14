#!/usr/bin/env bash
# Configura o kTsentinel Guard no notebook via SSH, perguntando segredos no SEU terminal.
# Nada é gravado neste PC nem no git; o config.toml nasce com permissão 600 no notebook.
# Uso: scripts/setup_remote.sh [host_ssh=note] [caminho_chave_fcm.json]
set -euo pipefail
HOST="${1:-note}"
KEY="${2:-}"
DVR="${DVR_HOST:-192.168.0.10}"

read -rp "Usuário do DVR: " DVR_USER
read -rsp "Senha do DVR: " DVR_PASS; echo
read -rp "Firebase project_id: " PROJECT
read -rp "Tópico FCM (sentinel_<32 hex>): " TOPIC
[[ "$TOPIC" =~ ^[A-Za-z0-9_.~%-]+$ ]] || { echo "tópico inválido"; exit 1; }
read -rp "Quantidade de câmeras (canais) [4]: " N; N="${N:-4}"

ssh "$HOST" 'umask 077; mkdir -p ~/.config/ktsentinel-guard; chmod 700 ~/.config/ktsentinel-guard'

{
  printf '[general]\narmed = true\ncooldown_seconds = 60\n\n'
  printf '[fcm]\nproject_id = "%s"\nservice_account_file = "~/.config/ktsentinel-guard/fcm-service-account.json"\n' "$PROJECT"
  printf 'topic = "%s"\ntitle = "kTsentinel - Casa"\n\n' "$TOPIC"
  for ((i = 1; i <= N; i++)); do
    printf '[[cameras]]\nid = %d\nname = "Camera %d"\n' "$i" "$i"
    printf 'rtsp_url = "rtsp://%s:554/user=%s&password=%s&channel=%d&stream=0.sdp"\n' "$DVR" "$DVR_USER" "$DVR_PASS" "$i"
    printf 'rtsp_url_secondary = "rtsp://%s:554/user=%s&password=%s&channel=%d&stream=1.sdp"\n' "$DVR" "$DVR_USER" "$DVR_PASS" "$i"
    printf 'is_active = true\n\n'
  done
} | ssh "$HOST" 'umask 077; cat > ~/.config/ktsentinel-guard/config.toml; chmod 600 ~/.config/ktsentinel-guard/config.toml'

if [[ -n "$KEY" ]]; then
  ssh "$HOST" 'umask 077; cat > ~/.config/ktsentinel-guard/fcm-service-account.json; chmod 600 ~/.config/ktsentinel-guard/fcm-service-account.json' < "$KEY"
fi
unset DVR_PASS
ssh "$HOST" 'ls -l ~/.config/ktsentinel-guard/'
echo "Pronto. Avise o assistente para rodar check-config e test-camera."
