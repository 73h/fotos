# GPU-Accelerations-Setup fuer Fotos WebUI

Write-Host "=== GPU-Acceleration Setup fuer Fotos ===" -ForegroundColor Cyan
Write-Host ""

# 1. YOLO
Write-Host "1. YOLO-Konfiguration (Objekt-/Personenerkennung)" -ForegroundColor Yellow
Write-Host "   Nutzt GPU fuer Echtzeit-Objekterkennung"
$env:FOTOS_YOLO_DEVICE = "auto"
$env:FOTOS_YOLO_MODEL = "yolov8n.pt"
$env:FOTOS_YOLO_CONF = "0.25"
Write-Host "   OK FOTOS_YOLO_DEVICE = $($env:FOTOS_YOLO_DEVICE)"
Write-Host ""

# 2. InsightFace
Write-Host "2. InsightFace-Konfiguration (Gesichtserkennung)" -ForegroundColor Yellow
Write-Host "   Nutzt GPU fuer schnelle Gesichts-Embeddings"
$env:FOTOS_PERSON_BACKEND = "insightface"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_l"
$env:FOTOS_INSIGHTFACE_CTX = "0"
$env:FOTOS_INSIGHTFACE_DET_SIZE = "640,640"
$env:FOTOS_QUIET_INFERENCE = "1"
Write-Host "   OK FOTOS_INSIGHTFACE_CTX = $($env:FOTOS_INSIGHTFACE_CTX) (GPU-Kontext)"
Write-Host ""

# 3. Timelapse AI
Write-Host "3. Timelapse AI-Enhancement (Video-Verbesserung)" -ForegroundColor Yellow
$env:FOTOS_TIMELAPSE_AI_BACKEND = "auto"
$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER = "auto"
Write-Host "   OK FOTOS_TIMELAPSE_AI_BACKEND = $($env:FOTOS_TIMELAPSE_AI_BACKEND)"
Write-Host ""

# Summary
Write-Host "OK GPU-Acceleration aktiviert!" -ForegroundColor Green
Write-Host ""
Write-Host "Konfigurierte Services:" -ForegroundColor Cyan
Write-Host "  * YOLO (Objekterkennung)      -> GPU"
Write-Host "  * InsightFace (Gesichter)     -> GPU (Kontext 0)"
Write-Host "  * Timelapse AI                -> Auto-Modus"
Write-Host ""
Write-Host "Naechste Schritte:" -ForegroundColor Cyan
Write-Host "  1. WebUI starten: python src/main.py web"
Write-Host "  2. Doctor: python src/main.py doctor"
Write-Host ""

