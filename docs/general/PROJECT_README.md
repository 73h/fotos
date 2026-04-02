# Fotos - lokale Foto-Suche mit Personen, Alben und Timelapse

`fotos` ist eine lokale Foto-Suchmaschine fuer Windows/Linux/Mac.
Sie scannt grosse Bildbestaende, legt einen SQLite-Index an und bietet dir eine schnelle Suche per CLI und Web-UI.

Der Fokus liegt auf:
- **lokal-first** (deine Bilder bleiben auf deinem Rechner)
- **inkrementeller Indexierung** (nur Neues wird nachgeladen)
- **personenzentrierter Suche** (inkl. Smile-Filter)
- **Album-Workflows** bis hin zu **Aging-Timelapse-Videos mit Morphing-Effekt**

## Was das Projekt kann

- Rekursives Scannen mehrerer Foto-Ordner
- Inkrementeller Index mit SQLite (`data/photo_index.db`)
- Duplikaterkennung (SHA1, optional near duplicates per pHash)
- Objekt-/Szenen-Labels mit YOLOv8 (`person`, `animal`, `object`)
- Personen einlernen ueber Referenzbilder (`enroll`)
- Personensuche per Name (`search-person`, `person:<name>`)
- Smile-Filter in Suche und Web-API (`smile>=...`)
- Alben anlegen, umbenennen, Cover setzen, Fotos zuweisen
- `Ref:`-Alben als Personenreferenz nutzen und Personen daraus per InsightFace neu anlernen
- Aging-Timelapse pro Album+Person als MP4 (CLI + Web-API + Download)
- Weboberflaeche mit Thumbnail-Cache, Pagination und API-Endpunkten

## Setup

### Voraussetzungen

- Python **3.11** oder **3.12**
- `pip` (aktuell)
- Optional fuer bessere Personenqualitaet: NVIDIA GPU + passendes PyTorch/ONNX Runtime Setup

### Installation

```powershell
Set-Location "D:\Code\fotos"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Optional fuer echte Gesichts-Embeddings (InsightFace):

```powershell
python -m pip install -r requirements-face.txt
```

## Schnellstart

```powershell
python src/main.py doctor
python src/main.py index --root "D:\MeineFotos"
python src/main.py web
```

Danach im Browser oeffnen: `http://127.0.0.1:5000`

## Typische Workflows

### 1) Suchen

```powershell
python src/main.py search --query "hund strand" --limit 20
python src/main.py search --query "person:marie smile>=0.6" --limit 50
python src/main.py search --query "person:marie person:max" --limit 50
```

### 2) Person einlernen und suchen

```powershell
python src/main.py enroll --name "Marie" --root "D:\Referenzbilder\Marie"
python src/main.py search-person --name "Marie" --limit 30
python src/main.py search-person --name "Marie" --max-persons 1 --limit 30
```

### 3) Smile-Score aktualisieren (ohne kompletten Reindex)

Wenn du nur Personenmatching/Smile-Scores neu berechnen willst, brauchst du **keinen** Voll-Reindex:

```powershell
python src/main.py rematch-persons --workers 4
```

### 4) Album-Timelapse (Aging + Morphing)

CLI:

```powershell
python src/main.py album-timelapse --album-id 1 --person "Marie" --output "data\cache\exports\marie_aging.mp4"
python src/main.py album-timelapse --album-id 1 --person "Marie" --output "data\cache\exports\marie_max.mp4" --quality max --interpolator flow --temporal-smooth 0.3 --detail-boost 0.4 --enhance-faces --ai-mode auto --ai-backend onnx --ai-strength 0.7
```

Web-API (Start + Download):

```powershell
curl -Method POST "http://127.0.0.1:5000/api/albums/1/timelapse" -ContentType "application/json" -Body '{"person":"Marie","fps":24,"hold":24,"morph":48,"size":512,"quality":"max","interpolator":"flow","temporal_smooth":0.3,"detail_boost":0.4,"enhance_faces":true,"ai_mode":"auto","ai_backend":"onnx","ai_strength":0.7}'
curl "http://127.0.0.1:5000/api/albums/timelapse/status/album_1_marie"
curl "http://127.0.0.1:5000/api/albums/timelapse/download/album_1_marie" -OutFile "marie_aging.mp4"
```

Hinweis zu den Profilen:
- `compat`: bisheriges Verhalten (Morphing, schnell)
- `balanced`: bessere Uebergaenge + leichtes Enhancement
- `max`: staerkere Glaettung/Detailverbesserung (langsamer), optional mit experimentellem KI-Hook (`ai_mode`, `ai_strength`)

### 5) Personen ueber Ref-Alben neu anlernen

Wenn ein Album mit `Ref:` beginnt, wird der Name nach dem Doppelpunkt als Personenname verwendet.

Beispiel:
- Albumname: `Ref: Marie`
- Wirkung: In der Web-UI erscheint im Album-Menue die Aktion **Person anlernen**

Dabei werden alle Bilder des Albums als Referenzmaterial verwendet und die Referenzen der Person
explizit mit **InsightFace** neu aufgebaut.

## Wichtige CLI-Kommandos

```powershell
python src/main.py --help
python src/main.py index --root "D:\MeineFotos" --index-workers 8
python src/main.py index --root "D:\MeineFotos" --near-duplicates --phash-threshold 6
python src/main.py update-exif
python src/main.py rematch-persons --workers 4
python src/main.py web --host 0.0.0.0 --port 5050
```

## Konfiguration (optional)

```powershell
$env:FOTOS_YOLO_MODEL="yolov8n.pt"
$env:FOTOS_YOLO_CONF="0.25"
$env:FOTOS_YOLO_DEVICE="auto"         # auto | 0 | cpu  (auto = GPU wenn verfuegbar)
$env:FOTOS_PERSON_BACKEND="auto"      # auto | insightface | histogram
$env:FOTOS_PERSON_THRESHOLD="0.38"
$env:FOTOS_PERSON_TOP_K="3"
$env:FOTOS_INSIGHTFACE_MODEL="buffalo_l"
$env:FOTOS_INSIGHTFACE_CTX="0"
$env:FOTOS_INSIGHTFACE_DET_SIZE="640,640"
$env:FOTOS_QUIET_INFERENCE="1"

# Optional: experimenteller Timelapse-AI-Backend-Resolver
$env:FOTOS_TIMELAPSE_AI_BACKEND="auto"      # auto | local | onnx | superres
$env:FOTOS_TIMELAPSE_SUPERRES_MODEL="D:\models\ESPCN_x2.pb"
$env:FOTOS_TIMELAPSE_SUPERRES_NAME="espcn"  # z.B. espcn | edsr | lapsrn | fsrcnn
$env:FOTOS_TIMELAPSE_SUPERRES_SCALE="2"     # 2..4

# Optional: ONNX Face-Enhancer (wird bei ai_backend=onnx oder auto genutzt)
$env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL="D:\models\face_enhancer.onnx"
$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER="auto" # auto | cuda | cpu
$env:FOTOS_TIMELAPSE_FACE_ONNX_SIZE="256"
```

## Tests

```powershell
python -m pytest -q
```
