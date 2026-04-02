#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Einmaliges Setup-Skript fuer Timelapse AI-Backends (SuperRes + ONNX Runtime).
    Ausfuehren: powershell -ExecutionPolicy Bypass -File scripts\setup_timelapse_models.ps1
#>

$PROJECT_ROOT = (Resolve-Path "$PSScriptRoot\..").Path
$MODELS_DIR = "D:\models"
$VENV = "$PROJECT_ROOT\.venv\Scripts\Activate.ps1"
$SUPERRES_PATH = "$MODELS_DIR\ESPCN_x2.pb"
$ONNX_PATH = "$MODELS_DIR\face_enhancer.onnx"
$ENV_FILE = "$PROJECT_ROOT\.env.timelapse"

Write-Host ""
Write-Host "=== Fotos Timelapse Model Setup ===" -ForegroundColor Cyan
Write-Host "Projekt: $PROJECT_ROOT"
Write-Host "Modell-Ordner: $MODELS_DIR"
Write-Host ""

# ---------------------------------------------------------------------------
# 0. Venv aktivieren
# ---------------------------------------------------------------------------
Write-Host "[1/6] Aktiviere .venv ..." -ForegroundColor Yellow
if (-not (Test-Path $VENV)) {
    Write-Warning "Kein .venv gefunden unter $VENV"
    Write-Warning "Bitte erst: python -m venv .venv"
    exit 1
}
& $VENV
Write-Host "      OK"

# ---------------------------------------------------------------------------
# 1. Modell-Ordner anlegen
# ---------------------------------------------------------------------------
Write-Host "[2/6] Lege Modell-Ordner an: $MODELS_DIR" -ForegroundColor Yellow
New-Item -ItemType Directory -Force $MODELS_DIR | Out-Null
Write-Host "      OK"

# ---------------------------------------------------------------------------
# 2. ONNX Runtime installieren
# ---------------------------------------------------------------------------
Write-Host "[3/6] Installiere onnxruntime ..." -ForegroundColor Yellow

$GPU_OK = $false
try {
    python -m pip install --upgrade "onnxruntime-gpu" --quiet 2>&1 | Out-Null
    $GPU_OK = $true
} catch {
    $GPU_OK = $false
}

if (-not $GPU_OK) {
    Write-Warning "onnxruntime-gpu nicht installierbar, installiere CPU-Version."
    python -m pip install --upgrade "onnxruntime" --quiet 2>&1 | Out-Null
}

$PROVIDER_SCRIPT = "import onnxruntime as ort; print(','.join(ort.get_available_providers()))"
$PROVIDERS = python -c $PROVIDER_SCRIPT
if ($GPU_OK) {
    Write-Host "      onnxruntime-gpu installiert. Provider: $PROVIDERS" -ForegroundColor Green
} else {
    Write-Host "      onnxruntime (CPU) installiert. Provider: $PROVIDERS" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. SuperRes-Modell herunterladen
# ---------------------------------------------------------------------------
Write-Host "[4/6] SuperRes-Modell (ESPCN x2) herunterladen ..." -ForegroundColor Yellow

if (Test-Path $SUPERRES_PATH) {
    $SIZE = [Math]::Round((Get-Item $SUPERRES_PATH).Length / 1KB, 1)
    Write-Host "      Bereits vorhanden ($SIZE KB) - wird uebersprungen." -ForegroundColor Green
} else {
    $SUPERRES_URL = "https://raw.githubusercontent.com/fannymonori/TF-ESPCN/master/export/ESPCN_x2.pb"
    Write-Host "      Lade von github.com/fannymonori/TF-ESPCN ..."
    $DOWNLOAD_SCRIPT = @"
import urllib.request, sys
req = urllib.request.Request('$SUPERRES_URL', headers={'User-Agent': 'python-urllib'})
with urllib.request.urlopen(req) as r, open(r'$SUPERRES_PATH', 'wb') as f:
    f.write(r.read())
print('OK')
"@
    try {
        python -c $DOWNLOAD_SCRIPT
        $SIZE = [Math]::Round((Get-Item $SUPERRES_PATH).Length / 1KB, 1)
        Write-Host "      OK - ESPCN_x2.pb ($SIZE KB)" -ForegroundColor Green
    } catch {
        Write-Warning "Download fehlgeschlagen. Manuell ablegen: $SUPERRES_PATH"
        Write-Warning "Quelle: $SUPERRES_URL"
    }
}

# ---------------------------------------------------------------------------
# 4. ONNX Face-Enhancer herunterladen (automatisch via check_onnx_models.py)
# ---------------------------------------------------------------------------
Write-Host "[5/6] ONNX Face-Enhancer pruefen/herunterladen ..." -ForegroundColor Yellow

if (Test-Path $ONNX_PATH) {
    $SIZE = [Math]::Round((Get-Item $ONNX_PATH).Length / 1KB / 1024, 1)
    Write-Host "      Vorhanden: $ONNX_PATH ($SIZE MB)" -ForegroundColor Green
} else {
    Write-Host "      Starte automatischen Modell-Download ..."
    python "$PSScriptRoot\check_onnx_models.py"
    if (Test-Path $ONNX_PATH) {
        $SIZE = [Math]::Round((Get-Item $ONNX_PATH).Length / 1KB / 1024, 1)
        Write-Host "      OK - face_enhancer.onnx ($SIZE MB)" -ForegroundColor Green
    } else {
        Write-Warning "Kein ONNX-Modell heruntergeladen. Timelapse laeuft mit superres+local weiter."
    }
}

# ---------------------------------------------------------------------------
# 5. .env.timelapse schreiben + aktuelle Shell setzen
# ---------------------------------------------------------------------------
Write-Host "[6/6] ENV-Konfigurationsdatei schreiben ..." -ForegroundColor Yellow

$ENVLINES = @(
    "# Timelapse AI Model Configuration"
    "# Generiert von setup_timelapse_models.ps1"
    "# Einbinden mit: . `".env.timelapse`""
    ""
    ('$env:FOTOS_TIMELAPSE_SUPERRES_MODEL="' + $SUPERRES_PATH + '"')
    ('$env:FOTOS_TIMELAPSE_SUPERRES_NAME="espcn"')
    ('$env:FOTOS_TIMELAPSE_SUPERRES_SCALE="2"')
    ""
    ('$env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL="' + $ONNX_PATH + '"')
    ('$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER="auto"')
    ('$env:FOTOS_TIMELAPSE_FACE_ONNX_SIZE="256"')
)

$ENVLINES | Set-Content $ENV_FILE -Encoding UTF8
Write-Host "      Geschrieben: $ENV_FILE"

# ONNX-Pfad aktualisieren, falls check_onnx_models.py ihn geaendert hat
if (Test-Path $ONNX_PATH) {
    $env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL = $ONNX_PATH
}

$env:FOTOS_TIMELAPSE_SUPERRES_MODEL = $SUPERRES_PATH
$env:FOTOS_TIMELAPSE_SUPERRES_NAME = "espcn"
$env:FOTOS_TIMELAPSE_SUPERRES_SCALE = "2"
$env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL = $ONNX_PATH
$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER = "auto"
$env:FOTOS_TIMELAPSE_FACE_ONNX_SIZE = "256"
Write-Host "      ENV fuer aktuelle Shell gesetzt." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Selbsttest ===" -ForegroundColor Cyan

$SELFTEST = @"
import sys, os
sys.path.insert(0, r'$PROJECT_ROOT')
os.environ['FOTOS_TIMELAPSE_SUPERRES_MODEL'] = r'$SUPERRES_PATH'
os.environ['FOTOS_TIMELAPSE_SUPERRES_NAME'] = 'espcn'
os.environ['FOTOS_TIMELAPSE_FACE_ONNX_MODEL'] = r'$ONNX_PATH'
from src.app.albums.timelapse_ai import resolve_enhancer
print('superres-backend:', type(resolve_enhancer('max', ai_backend='superres')).__name__)
print('onnx-backend:    ', type(resolve_enhancer('max', ai_backend='onnx')).__name__)
print('auto-backend:    ', type(resolve_enhancer('max', ai_backend='auto')).__name__)
"@

python -c $SELFTEST

# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Setup abgeschlossen ===" -ForegroundColor Green
Write-Host ""
Write-Host "Naechste Schritte:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  (A) ENV fuer neue Shell einbinden:"
Write-Host "      . '$ENV_FILE'"
Write-Host ""
Write-Host "  (B) Web starten:"
Write-Host "      python src/main.py web"
Write-Host ""
Write-Host "  (C) CLI-Test mit SuperRes:"
$CLI_EXAMPLE = 'python src/main.py album-timelapse --album-id 1 --person "NAME" --output data\cache\exports\test_sr.mp4 --quality max --interpolator flow --ai-mode max --ai-backend superres --ai-strength 0.65 --temporal-smooth 0.30 --detail-boost 0.35 --enhance-faces'
Write-Host "      $CLI_EXAMPLE"
Write-Host ""
Write-Host "  (D) Nach ONNX-Modell-Ablage:"
Write-Host "      Datei ablegen: $ONNX_PATH"
Write-Host "      Dann --ai-backend onnx verwenden."
Write-Host ""

