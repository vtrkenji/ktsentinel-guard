# Instala o kTsentinel Guard no Windows (usuario atual).
# Uso: na pasta extraida, powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
# Nao pede segredos. Depois edite %USERPROFILE%\.config\ktsentinel-guard\config.toml
# e copie a chave Firebase para fcm-service-account.json.
$ErrorActionPreference = "Stop"

$Src = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $env:LOCALAPPDATA "ktsentinel-guard\venv"
$Conf = Join-Path $env:USERPROFILE ".config\ktsentinel-guard"
$Py = Join-Path $Venv "Scripts\python.exe"
$Guard = Join-Path $Venv "Scripts\sentinel-guard.exe"

New-Item -ItemType Directory -Force -Path $Conf | Out-Null

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Error "Instale o Python 3.11+ em python.org e marque 'py launcher'."
}

if (-not (Test-Path $Py)) {
  py -3 -m venv $Venv
}

& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q $Src "opencv-python-headless>=4.8"

if (-not (Test-Path (Join-Path $Conf "config.toml"))) {
  Copy-Item (Join-Path $Src "packaging\config.example.toml") (Join-Path $Conf "config.toml")
}

$TaskName = "kTsentinel-Guard"
schtasks /Create /F /TN $TaskName /SC ONLOGON /RL LIMITED /TR "`"$Guard`" run" | Out-Null
Start-Process -FilePath $Guard -ArgumentList "run" -WindowStyle Hidden

Write-Host ""
Write-Host "Guard instalado em $Venv"
Write-Host "Proximos passos:"
Write-Host "  1. Edite $Conf\config.toml (cameras e topico: sentinel-guard gen-topic)"
Write-Host "  2. Copie a chave Firebase para $Conf\fcm-service-account.json"
Write-Host "  3. & `"$Guard`" check-config"
Write-Host "  4. powershell -File `"$Src\scripts\download_models.ps1`""
Write-Host "A tarefa $TaskName sobe o Guard em todo logon. Ele ja foi iniciado agora."
