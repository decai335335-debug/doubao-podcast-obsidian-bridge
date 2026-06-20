$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

& "$projectRoot\build_frontend_exe.ps1"

$releaseDir = Join-Path $projectRoot "release"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

$version = Get-Date -Format "yyyyMMdd-HHmmss"
$zipPath = Join-Path $releaseDir "DoubaoPodcastBridge-win64-$version.zip"
$latestZip = Join-Path $releaseDir "DoubaoPodcastBridge-win64-latest.zip"
$distDir = Join-Path $projectRoot "dist\DoubaoPodcastBridge"

if (-not (Test-Path $distDir)) {
    throw "Missing dist folder: $distDir"
}

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
if (Test-Path $latestZip) {
    Remove-Item -LiteralPath $latestZip -Force
}

Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
Copy-Item -LiteralPath $zipPath -Destination $latestZip -Force

Write-Host ""
Write-Host "Release package created:" -ForegroundColor Green
Write-Host $zipPath
Write-Host $latestZip
