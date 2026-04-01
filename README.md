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
```

Web-API (Start + Download):

```powershell
curl -Method POST "http://127.0.0.1:5000/api/albums/1/timelapse" -ContentType "application/json" -Body '{"person":"Marie","fps":24,"hold":24,"morph":48,"size":512}'
curl "http://127.0.0.1:5000/api/albums/timelapse/status/album_1_marie"
curl "http://127.0.0.1:5000/api/albums/timelapse/download/album_1_marie" -OutFile "marie_aging.mp4"
```

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
$env:FOTOS_PERSON_BACKEND="auto"      # auto | insightface | histogram
$env:FOTOS_PERSON_THRESHOLD="0.38"
$env:FOTOS_PERSON_TOP_K="3"
$env:FOTOS_INSIGHTFACE_MODEL="buffalo_l"
$env:FOTOS_INSIGHTFACE_CTX="0"
$env:FOTOS_INSIGHTFACE_DET_SIZE="640,640"
$env:FOTOS_QUIET_INFERENCE="1"
```

## Tests

```powershell
python -m pytest -q
```
