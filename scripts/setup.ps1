#Requires -Version 5.1
<#
.SYNOPSIS
    One-time setup for Jarvis: interpreter, virtual environment, dependencies.

.DESCRIPTION
    Creates a Python 3.12 environment OUTSIDE OneDrive and installs everything.

    Why outside: a virtual environment holding torch is several gigabytes of tens
    of thousands of files, which OneDrive syncs slowly and badly. It is entirely
    regenerable, so nothing precious leaves this folder -- only the bulk does.
    Code, configuration, memory and the audit log all stay here.

    Python 3.12 specifically: torch 2.6 / chatterbox-tts / ctranslate2 have no
    wheels for 3.13+, and this machine ships 3.14. uv installs 3.12 privately
    (no administrator rights, no PATH changes, nothing system-wide).

.PARAMETER Voice
    Also install the speech stack: faster-whisper, Chatterbox, openWakeWord.

.PARAMETER Gpu
    Install the CUDA build of torch (needs an NVIDIA card and driver). The
    default is the CPU build, which is what this machine's 4 GB card wants anyway:
    whisper takes the GPU, and the cloned voice runs on the CPU.

.PARAMETER NoDev
    Skip dev/test dependencies (pytest, ruff, playwright).

.EXAMPLE
    .\scripts\setup.ps1 -Voice
#>
[CmdletBinding()]
param(
    [switch]$Voice,
    [switch]$Gpu,
    [switch]$NoDev
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$JarvisHome = Join-Path $env:LOCALAPPDATA 'Jarvis'
$VenvDir = Join-Path $JarvisHome 'venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

# Keep every large, regenerable artifact off OneDrive. Exporting these also makes
# the values visible to every uv/pip/python child process this script spawns.
$env:UV_PROJECT_ENVIRONMENT = $VenvDir
$env:HF_HOME = Join-Path $env:USERPROFILE '.cache\huggingface'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $JarvisHome 'ms-playwright'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'   # Windows needs Developer Mode for symlinks
$env:PYTHONUTF8 = '1'

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "    $message" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Jarvis setup" -ForegroundColor White
Write-Host "  project     $Root"
Write-Host "  environment $VenvDir"
Write-Host "  model cache $env:HF_HOME"
Write-Host ""

# --- prerequisites ---------------------------------------------------------

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw @"
uv is required but was not found on PATH.

Install it with one of:
  winget install --id=astral-sh.uv -e
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

Then open a new terminal and run this script again.
"@
}

Push-Location $Root
try {
    Write-Step 'Installing Python 3.12 (private to uv, no admin rights)'
    & uv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed with exit code $LASTEXITCODE" }

    if (Test-Path $VenvPython) {
        Write-Step "Reusing the existing environment at $VenvDir"
    } else {
        Write-Step "Creating the environment at $VenvDir"
        New-Item -ItemType Directory -Force -Path $JarvisHome | Out-Null
        & uv venv --python 3.12 $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "uv venv failed with exit code $LASTEXITCODE" }
    }

    Write-Step 'Installing core dependencies'
    & uv pip install --python $VenvPython -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "core dependency install failed" }

    if (-not $NoDev) {
        Write-Step 'Installing development dependencies (pytest, ruff, playwright)'
        & uv pip install --python $VenvPython -r requirements-dev.txt
    }

    if ($Voice) {
        # Order matters: torch must already satisfy chatterbox-tts, or pip pulls a
        # second multi-gigabyte build of it from PyPI.
        if ($Gpu) {
            Write-Step 'Installing the CUDA build of torch (large download)'
            & uv pip install --python $VenvPython torch==2.6.0 torchaudio==2.6.0 `
                --index-url https://download.pytorch.org/whl/cu124
        } else {
            Write-Step 'Installing the CPU build of torch'
            & uv pip install --python $VenvPython torch==2.6.0 torchaudio==2.6.0
        }
        if ($LASTEXITCODE -ne 0) { throw "torch install failed" }

        Write-Step 'Installing the speech stack (whisper, Chatterbox, wake word)'
        & uv pip install --python $VenvPython -r requirements-voice.txt
        if ($LASTEXITCODE -ne 0) { throw "voice dependency install failed" }
    }

    if (-not (Test-Path (Join-Path $Root '.env'))) {
        Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
        Write-Step 'Created .env from .env.example'
    } else {
        Write-Step '.env already exists; leaving it alone'
    }

    if (-not (Test-Path (Join-Path $Root 'apps\orb\node_modules'))) {
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            Write-Step 'Installing the orb UI dependencies'
            Push-Location (Join-Path $Root 'apps\orb')
            try { & npm install --no-fund --no-audit } finally { Pop-Location }
        } else {
            Write-Warn 'npm not found: the orb UI cannot be installed. The gateway still works.'
        }
    }

    Write-Step 'Tuning the local brain (optional)'
    # Ollama 0.34 loads these models with a 65536-token context, which is fatal on a
    # small GPU: the model inflates to 10-13 GB, ~80% of it lands on the CPU, and
    # generation drops to a fraction of the speed -- a spoken turn then overruns its
    # timeout. The derived models cap the context at 8192 so they fit in VRAM.
    # Skipped silently when Ollama is absent -- a cloud key or a later install makes
    # this irrelevant.
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        Write-Warn 'ollama not found: skipping the tuned local brain (cloud keys still work).'
    }
    else {
        $installed = (& ollama list 2>$null | Out-String)
        $tuned = @(
            @{ Stock = 'llama3.2:3b'; Name = 'jarvis-brain:3b'; File = 'Modelfile.jarvis32' },
            @{ Stock = 'qwen3:4b';   Name = 'jarvis-brain:4b'; File = 'Modelfile.jarvis' }
        )
        foreach ($variant in $tuned) {
            if ($installed -match [regex]::Escape($variant.Name)) {
                Write-Host "    $($variant.Name) already present"
                continue
            }
            if ($installed -match [regex]::Escape($variant.Stock)) {
                & ollama create $variant.Name -f (Join-Path $Root ("config\ollama\" + $variant.File)) | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "    created $($variant.Name) (same weights as $($variant.Stock), 8k context, fits the GPU)"
                } else {
                    Write-Warn "could not create $($variant.Name); Jarvis will use $($variant.Stock) and be slow."
                }
            } else {
                Write-Warn "$($variant.Stock) is not installed yet: run 'ollama pull $($variant.Stock)', then re-run this script."
            }
        }
    }

    Write-Step 'Verifying the installation'
    & $VenvPython -c "import apps.gateway.main; print('gateway imports cleanly')"
    if ($LASTEXITCODE -ne 0) { throw "the gateway failed to import; see the traceback above" }

    Write-Host ""
    Write-Host "Ready." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Start everything:   .\scripts\run.ps1"
    Write-Host "  Run the checks:     .\scripts\verify.ps1"
    Write-Host ""
    if (-not $Voice) {
        Write-Host "  The speech stack is not installed, so Jarvis can currently only be typed to." -ForegroundColor Yellow
        Write-Host "  Add it with:        .\scripts\setup.ps1 -Voice"
        Write-Host ""
    } else {
        Write-Host "  The cloned voice needs a ~1.5 GB model download, done deliberately with:" -ForegroundColor Yellow
        Write-Host "      $VenvPython scripts\build_jarvis_voice.py --prefetch"
        Write-Host "  Until then Jarvis speaks with the Windows voice."
        Write-Host ""
    }
    Write-Host "  Without GEMINI_API_KEY in .env, Jarvis needs Ollama running:"
    Write-Host "      ollama serve          (in another window)"
    Write-Host "      ollama pull llama3.2:3b"
    Write-Host ""
    Write-Host "  That pull is all you need: setup.ps1 then derives 'jarvis-brain:3b'"
    Write-Host "  (the same weights with an 8k context) and Jarvis prefers it, because the"
    Write-Host "  stock context setting makes the model roughly four times slower here."
    Write-Host ""
}
finally {
    Pop-Location
}
