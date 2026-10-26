# Baixa o MobileNet-SSD (Caffe, ~23 MB) usado pelo filtro de pessoa.
$ErrorActionPreference = "Stop"
$dir = if ($args.Count -gt 0) { $args[0] } else { Join-Path $env:USERPROFILE ".local\share\ktsentinel-guard\models" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$base = "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master"
Invoke-WebRequest -Uri "$base/deploy.prototxt" -OutFile (Join-Path $dir "deploy.prototxt")
Invoke-WebRequest -Uri "$base/mobilenet_iter_73000.caffemodel" -OutFile (Join-Path $dir "mobilenet_iter_73000.caffemodel")
Write-Host "modelos em $dir"
