# GPU-Acceleration Guide für Fotos

## 🚀 Schnellstart - GPU aktivieren

### Windows (PowerShell):
```powershell
.\.venv\Scripts\Activate.ps1
. ./setup_gpu_acceleration.ps1
python src/main.py web
```

### Linux/macOS:
```bash
source .venv/bin/activate
# Umgebungsvariablen manuell setzen (siehe unten)
export FOTOS_YOLO_DEVICE="auto"
export FOTOS_INSIGHTFACE_CTX="0"
python src/main.py web
```

---

## 📋 Umgebungsvariablen für GPU

### 1️⃣ YOLO - Objekterkennung (Person, Tier, Objekt)

```powershell
$env:FOTOS_YOLO_DEVICE = "auto"        # GPU wenn verfügbar, sonst CPU
# Alternativen:
# $env:FOTOS_YOLO_DEVICE = "0"          # Spezifische GPU (0=erste GPU)
# $env:FOTOS_YOLO_DEVICE = "cpu"        # Nur CPU
```

**Was nutzt das?**
- Beim Index-Scanning: Alle Bilder werden gescannt
- Im Admin-Panel: "Index starten"
- In der WebUI: Objekt-Labels beim Hovern über Bilder

**Performance:**
- GPU: ~50-100ms pro Bild
- CPU: ~500-1000ms pro Bild
- **GPU ist 5-10x schneller** 🏃

---

### 2️⃣ InsightFace - Gesichtserkennung (Person-Matching)

```powershell
$env:FOTOS_PERSON_BACKEND = "insightface"    # GPU-fähiger Backend
$env:FOTOS_INSIGHTFACE_CTX = "0"             # GPU-Kontext (0 = erste GPU)
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_l"   # Modell-Qualität
$env:FOTOS_INSIGHTFACE_DET_SIZE = "640,640"  # Detektions-Größe
```

**GPU-Optionen:**
```
Kontext:          Bedeutung:
0                 Erste GPU (empfohlen)
1                 Zweite GPU (falls verfügbar)
-1                Nur CPU
```

**Modelle:**
```
buffalo_l         ✨ Beste Qualität + schnell (EMPFOHLEN)
buffalo_m         Mittlere Qualität
buffalo_s         Schnell, aber weniger genau
antelopev2        Ultra-schnell
```

**Was nutzt das?**
- Person-Suche: `person:marie`
- Person-Training: Ref-Alben
- Timelapse-Erstellung: Face Alignment
- Admin: "Rematch Persons"

**Performance:**
- GPU (buffalo_l): ~20-30ms pro Gesicht
- CPU (buffalo_l): ~200-400ms pro Gesicht
- **GPU ist 10x schneller** 🏃

---

### 3️⃣ Timelapse AI-Enhancement (optional)

```powershell
# Standard - Auto-Modus (empfohlen)
$env:FOTOS_TIMELAPSE_AI_BACKEND = "auto"          # auto | local | onnx | superres
```

#### A) Auto-Mode (Standard, funktioniert immer)
```powershell
$env:FOTOS_TIMELAPSE_AI_BACKEND = "auto"
```
- Nutzt verfügbare Backends in dieser Reihenfolge:
  1. Local (OpenCV - CPU-basiert)
  2. ONNX (wenn Modell + onnxruntime vorhanden)
  3. SuperRes (wenn Modell vorhanden)

#### B) ONNX Face-Enhancer (GPU-fähig, experimentell)
```powershell
$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER = "auto"   # auto | cuda | cpu
$env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL = "D:\models\face_enhancer.onnx"
$env:FOTOS_TIMELAPSE_FACE_ONNX_SIZE = "256"
```

**Voraussetzungen:**
```powershell
# Installiere onnxruntime mit GPU-Support
pip install onnxruntime[gpu]
```

**Was nutzt das?**
- Beim Erstellen von Timelapse-Videos mit `ai_mode=max`
- Face-Enhancement für bessere Video-Qualität

**Performance:**
- ONNX + GPU: ~50-100ms pro Frame
- Local (CPU): ~200-500ms pro Frame
- **GPU ist 3-5x schneller** 🏃

#### C) SuperRes-Upscaler (optional)
```powershell
$env:FOTOS_TIMELAPSE_SUPERRES_MODEL = "D:\models\ESPCN_x2.pb"
$env:FOTOS_TIMELAPSE_SUPERRES_NAME = "espcn"      # espcn, edsr, lapsrn, fsrcnn
$env:FOTOS_TIMELAPSE_SUPERRES_SCALE = "2"         # 2..4x Upscaling
```

---

## 🔍 GPU-Setup überprüfen

```powershell
python src/main.py doctor
```

**Ausgabe sollte zeigen:**
```
✓ YOLO Device: GPU (cuda:0) oder Auto
✓ InsightFace CTX: GPU (0)
✓ ONNX Runtime: Available
✓ ONNX Providers: [CUDAExecutionProvider, CPUExecutionProvider]
```

---

## 📊 Performance-Vergleich

### Index mit 10.000 Bildern

| Task | GPU | CPU | Speedup |
|------|-----|-----|---------|
| YOLO (Labeling) | 10 Min | 100+ Min | **10x** 🚀 |
| InsightFace | 5 Min | 50+ Min | **10x** 🚀 |
| Gesamt Index | 15-20 Min | 150-200 Min | **10x** 🚀 |

### Timelapse mit 50 Bildern

| Task | GPU | CPU | Speedup |
|------|-----|-----|---------|
| Face Extraction | 1 Min | 2 Min | 2x |
| AI Enhancement | 2-3 Min | 10-15 Min | **5-7x** 🚀 |
| Video Encoding | 1 Min | 1 Min | 1x (OpenCV) |
| **Gesamt** | **5 Min** | **15 Min** | **3x** |

---

## ⚙️ Empfohlene Konfigurationen

### Option 1: Maximum Performance (Standard)
```powershell
# GPU für alles nutzen
$env:FOTOS_YOLO_DEVICE = "auto"
$env:FOTOS_INSIGHTFACE_CTX = "0"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_l"
$env:FOTOS_TIMELAPSE_AI_BACKEND = "auto"
```

**Ideal für:** Schnelle Indizierung, häufige Timelapse-Generierung

---

### Option 2: CPU-Only (wenn keine GPU)
```powershell
$env:FOTOS_YOLO_DEVICE = "cpu"
$env:FOTOS_INSIGHTFACE_CTX = "-1"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_s"  # Schneller, weniger Qualität
```

**Ideal für:** Alte Hardware, Server ohne GPU

---

### Option 3: Balanced (Qualität + Speed)
```powershell
$env:FOTOS_YOLO_DEVICE = "auto"
$env:FOTOS_INSIGHTFACE_CTX = "0"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_m"
$env:FOTOS_TIMELAPSE_AI_BACKEND = "local"   # Ohne ONNX (weniger Speicher)
```

**Ideal für:** Mittlere Hardware, Balance zwischen Speed und RAM

---

## 🛠️ Troubleshooting

### Problem: "CUDA not available"
```powershell
# Lösung: PyTorch mit GPU-Support installieren
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### Problem: "InsightFace too slow"
```powershell
# Schnelleres Modell nutzen
$env:FOTOS_INSIGHTFACE_MODEL = "antelopev2"  # Sehr schnell
# Oder CPU-Backend
$env:FOTOS_INSIGHTFACE_CTX = "-1"
```

### Problem: "Out of GPU Memory"
```powershell
# Reduziere Detektions-Größe
$env:FOTOS_INSIGHTFACE_DET_SIZE = "480,480"  # Statt 640,640

# Oder nutze schnelleres Modell
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_s"
```

### Problem: "ONNX Runtime keine GPU"
```powershell
# Überprüfe Installation
python -c "import onnxruntime; print(onnxruntime.get_available_providers())"

# Sollte zeigen: ['CUDAExecutionProvider', 'CPUExecutionProvider']
# Wenn nicht, neu installieren:
pip uninstall onnxruntime -y
pip install onnxruntime[gpu]
```

---

## 📝 Checkliste für GPU-Setup

- [ ] NVIDIA GPU vorhanden?
- [ ] NVIDIA Treiber aktuell? (Prüfe mit `nvidia-smi`)
- [ ] CUDA Toolkit installiert?
- [ ] PyTorch mit GPU-Support? (`python -c "import torch; print(torch.cuda.is_available())"`)
- [ ] Umgebungsvariablen gesetzt? (Nutze `setup_gpu_acceleration.ps1`)
- [ ] `doctor` Command prüft alles? (`python src/main.py doctor`)
- [ ] WebUI startet ohne Fehler? (`python src/main.py web`)

---

## 🎯 Best Practices

### 1. Performance-Tuning für Index
```powershell
$env:FOTOS_YOLO_DEVICE = "auto"
$env:FOTOS_INSIGHTFACE_CTX = "0"
python src/main.py index --root "D:\Fotos" --index-workers 4
```
- `--index-workers 4`: Mehr Worker = besser GPU-Auslastung

### 2. Performance-Tuning für Timelapse
```powershell
# Nutze beste Qualität mit GPU
$env:FOTOS_TIMELAPSE_AI_BACKEND = "auto"

# In WebUI: Wähle "max" Quality + "flow" Interpolator
# + "auto" oder "onnx" AI-Backend
```

### 3. Speicher-Optimierung
```powershell
# Wenn nur 4GB VRAM verfügbar
$env:FOTOS_INSIGHTFACE_DET_SIZE = "320,320"
$env:FOTOS_INSIGHTFACE_MODEL = "buffalo_s"
# Timelapse AI-Backend = local (statt onnx)
```

---

## 📚 Weitere Ressourcen

- **PyTorch GPU Setup:** https://pytorch.org/get-started/locally/
- **ONNX Runtime GPU:** https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
- **InsightFace Models:** https://github.com/deepinsight/insightface/wiki/Model-Zoo

---

**Fragen?** Nutze `python src/main.py doctor` zur Diagnose! 🔧

