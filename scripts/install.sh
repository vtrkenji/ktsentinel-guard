#!/usr/bin/env bash
# Instala o kTsentinel Guard no Linux (usuário atual, systemd --user).
# Tudo fica em $HOME — funciona em Fedora Silverblue/Kinoite/Bazzite e outros
# sistemas imutáveis ( /usr somente leitura ). Não usa apt/dnf.
# Uso: dentro da pasta extraída do release, rode ./scripts/install.sh
# Não cria nem pede segredos: depois edite ~/.config/ktsentinel-guard/config.toml
# e copie a chave Firebase para ~/.config/ktsentinel-guard/fcm-service-account.json.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HOME/.local/share/ktsentinel-guard/venv"
CONF="$HOME/.config/ktsentinel-guard"

command -v python3 >/dev/null || { echo "python3 não encontrado"; exit 1; }

mkdir -p "$VENV" "$CONF" "$HOME/.config/systemd/user"
chmod 700 "$CONF"

# OpenCV do sistema (Debian/Fedora tradicional) evita baixar a wheel.
SYS_CV2=0
python3 -c "import cv2" 2>/dev/null && SYS_CV2=1

_make_venv() {
  local extra=()
  [ "$SYS_CV2" = 1 ] && extra+=(--system-site-packages)
  if python3 -c "import ensurepip" 2>/dev/null; then
    python3 -m venv "${extra[@]}" "$VENV"
    return
  fi
  python3 -m venv "${extra[@]}" --without-pip "$VENV"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$VENV/bin/python" -
}

if [ ! -x "$VENV/bin/python" ]; then
  _make_venv
elif [ "$SYS_CV2" = 1 ]; then
  # Recria com site-packages do sistema se o venv antigo não enxerga cv2.
  "$VENV/bin/python" -c "import cv2" 2>/dev/null || _make_venv
fi

PY="$VENV/bin/python"
if ! "$PY" -m pip --version >/dev/null 2>&1; then
  if python3 -c "import ensurepip" 2>/dev/null; then
    "$PY" -m ensurepip --upgrade
  else
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$PY" -
  fi
fi

"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q --force-reinstall --no-deps "$SRC"
"$PY" -m pip install -q "$SRC"
"$PY" -c "import cv2" 2>/dev/null || "$PY" -m pip install -q "opencv-python-headless>=4.8"

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
