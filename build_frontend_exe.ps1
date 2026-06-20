$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Host "== Doubao Podcast Bridge EXE build ==" -ForegroundColor Cyan
Write-Host "Project: $projectRoot"

$python = $env:DOUBAO_PYTHON_EXE
if (-not $python) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Write-Host "Python: $python"

& $python -m PyInstaller --noconfirm --clean .\DoubaoPodcastBridge.spec

$exe = Join-Path $projectRoot "dist\DoubaoPodcastBridge\DoubaoPodcastBridge.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe was not created."
}

Write-Host ""
Write-Host "Build complete:" -ForegroundColor Green
Write-Host $exe
Write-Host ""
Write-Host "Run it by double-clicking DoubaoPodcastBridge.exe."
Write-Host "Data files stay beside the EXE; structured task JSON stays in E:\Obsidian\主仓库\90-归档\DoubaoBridgeJson."
