#!/usr/bin/env bash
# Instala o kTsentinel Guard no Linux (usuário atual, systemd --user).
# Uso: dentro da pasta extraída do release, rode ./scripts/install.sh
# Não cria nem pede segredos: depois edite ~/.config/ktsentinel-guard/config.toml
# e copie a chave Firebase para ~/.config/ktsentinel-guard/fcm-service-account.json.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HOME/.local/share/ktsentinel-guard/venv"
CONF="$HOME/.config/ktsentinel-guard"

command -v python3 >/dev/null || { echo "python3 não encontrado"; exit 1; }
python3 -c "import cv2" 2>/dev/null || {
  echo "OpenCV não encontrado. Instale com: sudo apt install python3-opencv ffmpeg"; exit 1; }

mkdir -p "$VENV" "$CONF" "$HOME/.config/systemd/user"
chmod 700 "$CONF"
python3 -m venv --system-site-packages --without-pip "$VENV"
python3 -m pip --python "$VENV/bin/python" install -q --force-reinstall --no-deps "$SRC"
python3 -m pip --python "$VENV/bin/python" install -q "$SRC" || true

if [ ! -f "$CONF/config.toml" ]; then
  cp "$SRC/packaging/config.example.toml" "$CONF/config.toml"
  chmod 600 "$CONF/config.toml"
fi
cp "$SRC/packaging/sentinel-guard.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload

echo
echo "Guard instalado em $VENV"
echo "Próximos passos:"
echo "  1. Edite $CONF/config.toml (câmeras, tópico: sentinel-guard gen-topic)"
echo "  2. Copie a chave Firebase para $CONF/fcm-service-account.json (chmod 600)"
echo "  3. $VENV/bin/sentinel-guard check-config"
echo "  4. systemctl --user enable --now sentinel-guard"
