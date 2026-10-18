# Starts the J.A.R.V.I.S. Desktop System Tray Companion & Global Hotkey (Ctrl+Alt+J)
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$VenvPython = "$env:LOCALAPPDATA\Jarvis\venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Virtual environment not found at $VenvPython"
    exit 1
}

Write-Host "Starting J.A.R.V.I.S. System Tray Companion (Global Hotkey: Ctrl+Alt+J)..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    Start-Process -FilePath $VenvPython -ArgumentList "-m", "apps.tray.tray_app" -WorkingDirectory $ProjectRoot
    Write-Host "J.A.R.V.I.S. Tray Companion launched in background." -ForegroundColor Green
    Write-Host "Press Ctrl + Alt + J anytime to bring up J.A.R.V.I.S. Orb." -ForegroundColor Yellow
} finally {
    Pop-Location
}
