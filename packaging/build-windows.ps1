param(
  [string]$Name = "SignalDeck",
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Test-Path ".venv")) {
  py -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[build]"

& .\.venv\Scripts\pyinstaller.exe `
  --clean `
  --noconfirm `
  --name $Name `
  --add-data "config;config" `
  --add-data "docs;docs" `
  run_signaldeck.py

New-Item -ItemType Directory -Force -Path "dist\$Name\data" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\$Name\data\cache" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\$Name\data\reports" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\$Name\data\review" | Out-Null
Copy-Item -Recurse -Force "config" "dist\$Name\config"
Copy-Item -Recurse -Force "docs" "dist\$Name\docs"

Write-Host ""
Write-Host "Build complete: dist\$Name\$Name.exe"
Write-Host "Run it, then open http://127.0.0.1:$Port/ if the browser does not open automatically."
