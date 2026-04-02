# GPU-Nutzung: Schnelle Referenz

## TL;DR - GPU für alles einschalten

```powershell
.\.venv\Scripts\Activate.ps1
. ./setup_gpu.ps1
python src/main.py web
```

Das ist alles! ✅

---

## Was wird jetzt mit GPU beschleunigt?

| Feature | GPU-Speedup | Wann aktiv |
|---------|-------------|-----------|
| **YOLO Labels** | 10x schneller | Index-Scan, WebUI Hover |
| **InsightFace** | 10x schneller | Person-Suche, Timelapse |
| **Timelapse** | 3-5x schneller | Film erstellen (mit AI-Mode) |

---

## Konfigurationen nach Bedarf

### 1️⃣ Nur YOLO-GPU (schnelle Labeling)
```powershell
$env:FOTOS_YOLO_DEVICE = "auto"
$env:FOTOS_INSIGHTFACE_CTX = "-1"  # CPU nur
```

### 2️⃣ Nur InsightFace-GPU (schnelle Persons)
```powershell
$env:FOTOS_YOLO_DEVICE = "cpu"
$env:FOTOS_INSIGHTFACE_CTX = "0"   # GPU
```

### 3️⃣ Maximum Performance (Standard)
```powershell
. ./setup_gpu.ps1  # Alle GPU-Features
```

### 4️⃣ CPU-Only (kein GPU)
```powershell
$env:FOTOS_YOLO_DEVICE = "cpu"
$env:FOTOS_INSIGHTFACE_CTX = "-1"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_s"  # schneller
```

---

## Hardware-Anforderungen

| GPU | RAM | VRAM | Speedup |
|-----|-----|------|---------|
| RTX 4090 | 16GB | 24GB | 15x+ 🚀 |
| RTX 4070 | 16GB | 12GB | 10x 🚀 |
| RTX 3060 | 12GB | 12GB | 8x 🚀 |
| RTX 2080 | 16GB | 8GB | 6x 🚀 |
| CPU nur | 8GB | - | 1x |

---

## Troubleshooting

**GPU nicht erkannt?**
```powershell
python -c "import torch; print(torch.cuda.is_available())"
# Sollte: True
```

**InsightFace zu langsam?**
```powershell
$env:FOTOS_INSIGHTFACE_MODEL = "antelopev2"  # schneller
```

**GPU-Speicher voll?**
```powershell
$env:FOTOS_INSIGHTFACE_DET_SIZE = "480,480"  # kleiner statt 640,640
```

---

**Siehe auch:** `GPU_SETUP.md` für Vollständige Dokumentation

