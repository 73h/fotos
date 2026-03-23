# Fotos MVP (lokale Suche)

Dieses Projekt bietet ein lauffaehiges Grundgeruest fuer lokale Foto-Indexierung und Suche.

## Features im MVP

- rekursiver Foto-Scan eines Ordners
- SQLite-Index (`data/photo_index.db`)
- YOLOv8 Labels fuer `person`, `animal`, `object`
- Erkennung bestimmter Personen per Referenzbilder (`enroll`)
- Textsuche ueber Dateiname, Pfad und Labels

## Voraussetzungen

- Python 3.11 oder 3.12 empfohlen
- Windows PowerShell
- NVIDIA GPU + passendes PyTorch CUDA-Wheel

Abhaengigkeiten installieren:

```powershell
python -m pip install -r requirements.txt
```

Optional fuer echte Gesichtsembeddings (InsightFace):

```powershell
python -m pip install -r requirements-face.txt
```

## Schnellstart

```powershell
Set-Location "D:\Code\fotos"
.\.venv\Scripts\Activate.ps1
python src/main.py --help
```

System-Diagnose:

```powershell
python src/main.py doctor
```

Index bauen:

```powershell
python src/main.py index --root "D:\MeineFotos"
python src/main.py index --root "D:\MeineFotos" --person-backend auto
```

Suchen:

```powershell
python src/main.py search --query "hund strand"
python src/main.py search --query "person fahrrad" --limit 10
```

Bestimmte Person einlernen:

```powershell
python src/main.py enroll --name "Max" --root "D:\Referenzbilder\Max"
python src/main.py enroll --name "Max" --root "D:\Referenzbilder\Max" --person-backend insightface
```

Nach Person suchen:

```powershell
python src/main.py search-person --name "Max" --limit 20
python src/main.py search --query "person:max"
```

Schneller Selbsttest:

```powershell
python -m unittest tests.test_person_matching -v
```

## Naechster Schritt (GPU-Modelle)

`src/app/detectors/labels.py` nutzt YOLOv8 (Ultralytics) fuer die Kategorien
`person`, `animal`, `object`.

`src/app/persons/service.py` nutzt ein austauschbares Embedding-Backend:

- `insightface` (wenn verfuegbar): echte Gesichts-Embeddings
- `histogram`: robuster Fallback
- `auto` (Standard): versucht InsightFace, faellt sonst auf Histogramm zurueck

`place` wird im MVP weiter ueber Pfad-/Dateinamen-Schluesselwoerter erkannt.

Optional konfigurierbar:

```powershell
$env:FOTOS_YOLO_MODEL="yolov8n.pt"
$env:FOTOS_YOLO_CONF="0.25"
$env:FOTOS_PERSON_THRESHOLD="0.90"
$env:FOTOS_PERSON_TOP_K="3"
$env:FOTOS_PERSON_FULL_IMAGE_FALLBACK="1"
$env:FOTOS_PERSON_BACKEND="auto"
$env:FOTOS_INSIGHTFACE_MODEL="buffalo_l"
$env:FOTOS_INSIGHTFACE_CTX="0"
$env:FOTOS_INSIGHTFACE_DET_SIZE="640,640"
python src/main.py index --root "D:\MeineFotos"
```

