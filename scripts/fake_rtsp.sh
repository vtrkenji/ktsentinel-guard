#!/usr/bin/env bash
# Stream RTSP falso: cena parada com ruído; um objeto texturizado se move entre t=20s e t=30s de cada ciclo de 40s.
# (objeto animado: um bloco liso e repetido seria aprendido pelo MOG2 como fundo)
# Uso: scripts/fake_rtsp.sh [mediamtx_dir] ; stream em rtsp://127.0.0.1:8554/fake
set -euo pipefail
MTX_DIR="${1:-$HOME/ktg-test/bin}"
"$MTX_DIR/mediamtx" >/tmp/mediamtx.log 2>&1 &
MTX=$!
trap 'kill $MTX 2>/dev/null' EXIT
sleep 1
ffmpeg -loglevel error -re -f lavfi -i "color=c=0x606060:s=640x360:r=10" -f lavfi -i "testsrc2=s=80x100:r=10" \
  -filter_complex "[0]drawbox=x=100:y=200:w=440:h=30:color=0x808080:t=fill[b];\
[b][1]overlay=x=100+mod(t\,10)*40:y=100:enable=between(mod(t\,40)\,20\,30),noise=alls=4:allf=t,format=yuv420p" \
  -c:v libx264 -preset ultrafast -tune zerolatency -g 20 -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/fake
