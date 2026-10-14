#!/usr/bin/env bash
# Baixa o MobileNet-SSD (Caffe, ~23 MB) usado pelo filtro de pessoa.
set -euo pipefail
dir="${1:-$HOME/.local/share/ktsentinel-guard/models}"
mkdir -p "$dir"
base=https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master
curl -fsSL -o "$dir/deploy.prototxt" "$base/deploy.prototxt"
curl -fsSL -o "$dir/mobilenet_iter_73000.caffemodel" "$base/mobilenet_iter_73000.caffemodel"
echo "modelos em $dir"
