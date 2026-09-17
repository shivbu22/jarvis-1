#Requires -Version 5.1
<#
.SYNOPSIS
    Run every check: lint, unit tests, orb typecheck, then a live smoke test.

.DESCRIPTION
    Four stages, and the last one matters most. Unit tests prove the policy gate
    behaves; only a running server proves the routes, CORS headers and engines are
    wired together at all.

    The smoke stage starts its own gateway on a spare port so it can run while
    your own instance is already up, and stops it afterwards.

.PARAMETER SkipLive
    Skip the live gateway stage (lint, tests and typecheck only).

.PARAMETER Port
    Port for the temporary gateway in the live stage.

.EXAMPLE
    .\scripts\verify.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipLive,
    [int]$Port = 8010
)

$Root = Split-Path -Parent $PSScriptRoot
$JarvisHome = Join-Path $env:LOCALAPPDATA 'Jarvis'
$VenvDir = Join-Path $JarvisHome 'venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

$env:UV_PROJECT_ENVIRONMENT = $VenvDir
$env:HF_HOME = Join-Path $env:USERPROFILE '.cache\huggingface'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $JarvisHome 'ms-playwright'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = $Root

if (-not (Test-Path $VenvPython)) { throw 'No environment found. Run .\scripts\setup.ps1 first.' }

$failures = @()
$gateway = $null

function Stage([string]$Name, [scriptblock]$Body) {
    Write-Host ""
    Write-Host "=== $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        $script:failures += $Name
        Write-Host "    FAILED" -ForegroundColor Red
    } else {
        Write-Host "    ok" -ForegroundColor Green
    }
}

Push-Location $Root
try {
    Stage 'ruff (lint)' { & $VenvPython -m ruff check . }

    Stage 'pytest (unit and integration)' { & $VenvPython -m pytest -q }

    if (Test-Path (Join-Path $Root 'apps\orb\node_modules')) {
        Push-Location (Join-Path $Root 'apps\orb')
        try { Stage 'orb typecheck' { & npx tsc --noEmit } } finally { Pop-Location }
    } else {
        Write-Host ''
        Write-Host '=== orb typecheck' -ForegroundColor Cyan
        Write-Host '    skipped: run npm install in apps/orb first' -ForegroundColor Yellow
    }

    if (-not $SkipLive) {
        Write-Host ''
        Write-Host "=== live gateway on port $Port" -ForegroundColor Cyan
        $gateway = Start-Process -FilePath $VenvPython `
            -ArgumentList '-m', 'uvicorn', 'apps.gateway.main:app', '--host', '127.0.0.1', '--port', $Port `
            -WorkingDirectory $Root -PassThru -WindowStyle Hidden

        $url = "http://127.0.0.1:$Port"
        $ready = $false
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline -and -not $ready) {
            try {
                $ready = (Invoke-WebRequest -Uri "$url/health" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200
            } catch {
                if ($gateway.HasExited) { break }
                Start-Sleep -Milliseconds 500
            }
        }

        if (-not $ready) {
            $failures += 'live gateway did not start'
            Write-Host '    FAILED: the gateway never answered /health' -ForegroundColor Red
        } else {
            & $VenvPython scripts\smoke.py --url $url
            if ($LASTEXITCODE -ne 0) {
                $failures += 'smoke test'
                Write-Host '    FAILED' -ForegroundColor Red
            } else {
                Write-Host '    ok' -ForegroundColor Green
            }
        }
    }

    Write-Host ''
    if ($failures.Count -gt 0) {
        Write-Host "FAILED: $($failures -join ', ')" -ForegroundColor Red
        exit 1
    }
    Write-Host 'All checks passed.' -ForegroundColor Green
    exit 0
}
finally {
    if ($gateway -and -not $gateway.HasExited) {
        & taskkill /PID $gateway.Id /T /F 2>&1 | Out-Null
    }
    Pop-Location
}
