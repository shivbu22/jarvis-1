#Requires -Version 5.1
<#
.SYNOPSIS
    Start Jarvis: the gateway and the voice interface, in one command.

.DESCRIPTION
    Starts the FastAPI gateway on port 8000 and the orb UI on 5173, waits until
    both answer, prints the URLs, then holds the window open. Ctrl+C stops both.

    Ports 3111, 3112 and 8080 are used by other projects on this machine, which is
    why the gateway is on 8000 and the orb on 5173.

.PARAMETER GatewayOnly
    Do not start the browser interface (for a headless or API-only session).

.PARAMETER StartOllama
    Start `ollama serve` if it is not already running. Off by default: an
    assistant that silently spawns background servers is not a good neighbour.

.PARAMETER Port
    Override the gateway port.

.EXAMPLE
    .\scripts\run.ps1
.EXAMPLE
    .\scripts\run.ps1 -StartOllama
#>
[CmdletBinding()]
param(
    [switch]$GatewayOnly,
    [switch]$StartOllama,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$JarvisHome = Join-Path $env:LOCALAPPDATA 'Jarvis'
$VenvDir = Join-Path $JarvisHome 'venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

$env:UV_PROJECT_ENVIRONMENT = $VenvDir
$env:HF_HOME = Join-Path $env:USERPROFILE '.cache\huggingface'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $JarvisHome 'ms-playwright'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONUTF8 = '1'

if (-not (Test-Path $VenvPython)) {
    throw "No environment found. Run .\scripts\setup.ps1 first."
}

$orbPath = Join-Path $Root 'apps\orb'
$orbProcess = $null
$gatewayProcess = $null
$ollamaProcess = $null

function Test-Endpoint([string]$Url, [int]$Seconds = 2) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $Seconds -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-ForGateway([string]$Url, [int]$TimeoutSeconds = 90) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint $Url 2) { return $true }
        if ($gatewayProcess -and $gatewayProcess.HasExited) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

Push-Location $Root
try {
    # --- Ollama, the keyless brain -----------------------------------------
    $ollamaUp = Test-Endpoint 'http://127.0.0.1:11434/api/tags'
    if (-not $ollamaUp) {
        if ($StartOllama) {
            Write-Host '==> Starting ollama serve' -ForegroundColor Cyan
            $ollamaProcess = Start-Process -FilePath 'ollama' -ArgumentList 'serve' -PassThru -WindowStyle Hidden
            Start-Sleep -Seconds 2
        } else {
            Write-Host '!! Ollama is not running.' -ForegroundColor Yellow
            Write-Host '   Without GEMINI_API_KEY in .env there is no brain, so Jarvis cannot answer.'
            Write-Host '   Either run:  ollama serve      (and: ollama pull qwen3:4b)'
            Write-Host '   or rerun this with:  .\scripts\run.ps1 -StartOllama'
            Write-Host ''
        }
    } else {
        Write-Host '==> Ollama is running' -ForegroundColor Cyan
    }

    # --- gateway ------------------------------------------------------------
    Write-Host "==> Starting the gateway on http://127.0.0.1:$Port" -ForegroundColor Cyan
    $gatewayProcess = Start-Process -FilePath $VenvPython `
        -ArgumentList '-m', 'uvicorn', 'apps.gateway.main:app', '--host', '127.0.0.1', '--port', $Port `
        -WorkingDirectory $Root -PassThru -WindowStyle Hidden

    $healthUrl = "http://127.0.0.1:$Port/health"
    if (-not (Wait-ForGateway $healthUrl)) {
        if ($gatewayProcess.HasExited) { throw "The gateway exited immediately (code $($gatewayProcess.ExitCode))." }
        throw "The gateway did not answer $healthUrl within 90 seconds."
    }

    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 10 -UseBasicParsing
        Write-Host "    brain:  $($health.active_brain)   mode: $($health.mode)   tools: $($health.tool_count)" -ForegroundColor DarkGray
        Write-Host "    ears:   $($health.stt.model) on $($health.stt.device)" -ForegroundColor DarkGray
        Write-Host "    voice:  $($health.tts.engine)" -ForegroundColor DarkGray
        foreach ($note in $health.notes) { Write-Host "    note:   $note" -ForegroundColor Yellow }
    } catch {
        Write-Host '    (could not read /health, continuing anyway)' -ForegroundColor Yellow
    }

    # --- orb ----------------------------------------------------------------
    if (-not $GatewayOnly) {
        if (-not (Test-Path (Join-Path $orbPath 'node_modules'))) {
            Write-Host '==> Installing the orb UI dependencies (first run)' -ForegroundColor Cyan
            Push-Location $orbPath
            try { & npm install --no-fund --no-audit } finally { Pop-Location }
        }
        Write-Host '==> Starting the orb UI on http://127.0.0.1:5173' -ForegroundColor Cyan
        $orbProcess = Start-Process -FilePath 'npm' -ArgumentList 'run', 'dev' `
            -WorkingDirectory $orbPath -PassThru -WindowStyle Hidden

        $deadline = (Get-Date).AddSeconds(60)
        $orbUp = $false
        while ((Get-Date) -lt $deadline -and -not $orbUp) {
            $orbUp = Test-Endpoint 'http://127.0.0.1:5173' 2
            if (-not $orbUp) { Start-Sleep -Milliseconds 500 }
        }
        if (-not $orbUp) { Write-Host '!! The orb did not come up; the gateway is still usable.' -ForegroundColor Yellow }
    }

    Write-Host ''
    Write-Host 'Jarvis is running.' -ForegroundColor Green
    if (-not $GatewayOnly) {
        Write-Host '  Talk to it:  http://127.0.0.1:5173' -ForegroundColor White
        Write-Host '               hold HOLD TO TALK, or say "Hey Jarvis" after ENABLE WAKE WORD'
    }
    Write-Host "  Gateway:     $healthUrl"
    Write-Host "  Audit log:   http://127.0.0.1:$Port/audit"
    Write-Host ''
    Write-Host 'Press Ctrl+C to stop.' -ForegroundColor DarkGray

    Wait-Process -Id $gatewayProcess.Id
}
finally {
    Write-Host ''
    Write-Host 'Shutting down...' -ForegroundColor DarkGray
    if ($orbProcess -and -not $orbProcess.HasExited) {
        # npm spawns vite as a child, so the tree has to go, not just the parent.
        & taskkill /PID $orbProcess.Id /T /F 2>&1 | Out-Null
    }
    if ($gatewayProcess -and -not $gatewayProcess.HasExited) {
        & taskkill /PID $gatewayProcess.Id /T /F 2>&1 | Out-Null
    }
    if ($ollamaProcess -and -not $ollamaProcess.HasExited) {
        Write-Host 'Leaving Ollama running (it may be used by other tools).' -ForegroundColor DarkGray
    }
    Pop-Location
}
