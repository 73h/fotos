# Fotos MVP (lokale Suche)

Dieses Projekt bietet ein lauffaehiges Grundgeruest fuer lokale Foto-Indexierung und Suche.

## Features im MVP

- rekursiver Foto-Scan eines oder mehrerer Ordner
- inkrementelle Indexierung (Index kann schrittweise erweitert werden)
- optionale Parallelisierung der Index-Vorverarbeitung (`--index-workers`)
- SQLite-Index (`data/photo_index.db`)
- Duplikat-Markierung (exakt via SHA1, optional nahe Duplikate via pHash)
- YOLOv8 Labels fuer `person`, `animal`, `object`
- Erkennung bestimmter Personen per Referenzbilder (`enroll`)
- Alben anlegen, benennen und Fotos zuweisen
- Textsuche ueber Dateiname, Pfad und Labels
- Websuche mit Flask + HTMX, Thumbnail-Cache und Pagination

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

Index mit mehreren Root-Pfaden erweitern (inkrementell):

```powershell
python src/main.py index --root "D:\MeineFotos" --root "D:\UrlaubsFotos"
python src/main.py index --root "D:\Fotos2024" --root "D:\Fotos2025" --root "D:\Archiv"
```

Schneller/gezielter indexieren:

```powershell
python src/main.py index --root "D:\MeineFotos" --index-workers 8
python src/main.py index --root "D:\MeineFotos" --force-reindex
python src/main.py index --root "D:\MeineFotos" --near-duplicates --phash-threshold 6
```

- Ohne `--force-reindex` werden unveraenderte Dateien (Groesse + mtime) uebersprungen.
- `--index-workers` parallelisiert die Vorverarbeitung; DB-Schreiben bleibt stabil seriell.
- Exakte Duplikate werden immer markiert, near duplicates nur mit `--near-duplicates`.

Suchen:

```powershell
python src/main.py search --query "hund strand"
python src/main.py search --query "person fahrrad" --limit 10
```

Webanwendung starten:

```powershell
python src/main.py web
python src/main.py web --host 0.0.0.0 --port 5050
python src/main.py web --db "data/photo_index.db" --cache-dir "data/cache"
```

Dann im Browser oeffnen: `http://127.0.0.1:5000`

In der Web-UI kannst du rechts Alben anlegen und Bilder per Drag&Drop in ein Album ziehen.
Ein Klick auf ein Album aktiviert den Albumfilter fuer die Suche.

REST-Endpunkt fuer Integrationen:

```powershell
curl "http://127.0.0.1:5000/api/search?q=hund&page=1&per_page=24"
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

Nur Solo-Bilder (Person allein, keine anderen Personen im Bild):

```powershell
python src/main.py search-person --name "Max" --limit 20 --max-persons 1
```

Ueber Web-API (auch in der WebUI per Query-Parameter):

```powershell
curl "http://127.0.0.1:5000/api/search?q=person:max&max_persons=1"
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
$env:FOTOS_PERSON_THRESHOLD="0.38"
$env:FOTOS_PERSON_TOP_K="3"
$env:FOTOS_PERSON_FULL_IMAGE_FALLBACK="1"
$env:FOTOS_PERSON_BACKEND="insightface"
$env:FOTOS_INSIGHTFACE_MODEL="buffalo_l"
$env:FOTOS_INSIGHTFACE_CTX="0"
$env:FOTOS_INSIGHTFACE_DET_SIZE="640,640"
$env:FOTOS_QUIET_INFERENCE  = "1"
python src/main.py index --root "D:\MeineFotos"
```

## Web-App Details

- Suche in der Web-UI nutzt denselben SQLite-Index wie die CLI.
- Treffer werden als Thumbnail-Kacheln angezeigt.
- Thumbnails werden in `data/cache/thumbnails` persistent zwischengespeichert.
- Ergebnisse sind seitenweise abrufbar (`page`, `per_page`), auch ueber `/api/search`.
- Rechts koennen Alben angelegt werden; Trefferkarten lassen sich per Drag&Drop in Alben schieben.
- Albumfilter ist ueber die Album-Boxen in der Web-UI und ueber `album_id` in `/api/search` verfuegbar.

