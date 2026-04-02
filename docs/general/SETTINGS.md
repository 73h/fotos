# 🔧 Einstellungen & Konfiguration

Alle Einstellungen für das Fotos-Projekt werden jetzt zentral in der **SQLite-Datenbank** und im **Admin-Dashboard** verwaltet. Dies ersetzt die vorherigen ENV-Variablen.

## 📍 Admin-Dashboard

Zugriff über: `http://localhost:5000/admin`

### Registerkarten

#### 🎯 YOLO (Objekterkennung)

- **Modell**: Größe des YOLO-Modells
  - `yolov8n.pt` - schnell, gute Balance (default)
  - `yolov8s.pt` - bessere Qualität
  - `yolov8m.pt` - beste Qualität für maximale Genauigkeit
  - `yolov8l.pt` - sehr langsam, höchste Qualität

- **Konfidenz** (0.0-1.0)
  - Niedriger = mehr erkannte Objekte
  - Standard: 0.25 (optimal für Qualität)

- **Device**
  - GPU (CUDA) - deutlich schneller ⚡
  - CPU - Fallback

**Standard-Werte für Qualität + GPU:**
```json
{
  "yolo_model": "yolov8m.pt",
  "yolo_confidence": 0.15,
  "yolo_device": "0"
}
```

#### 👤 Personen-Matching

- **Backend**
  - `auto` - versucht InsightFace, Fallback auf Histogram
  - `insightface` - beste Qualität (empfohlen)
  - `histogram` - schneller Fallback

- **Matching-Schwelle** (0.0-1.0)
  - Niedriger = mehr Matches (liberal)
  - Höher = strenger
  - Standard: 0.38

- **Top-K Ergebnisse**
  - Wie viele beste Matches pro Foto zurückgeben
  - Standard: 3

- **Vollbild-Fallback**
  - Falls kein Gesicht erkannt wird, ganzes Foto als Fallback nutzen
  - Empfohlen: aktiviert

**Standard-Werte für Qualität:**
```json
{
  "person_backend": "insightface",
  "person_threshold": 0.38,
  "person_top_k": 3,
  "person_full_image_fallback": true
}
```

#### 🧠 InsightFace (Gesichtserkennung)

- **Modell**
  - `buffalo_l` - beste Qualität, mehr VRAM (empfohlen)
  - `buffalo_s` - gute Balance
  - `buffalo_sc` - schnell, weniger Speicher

- **GPU Device**
  - 0+ für GPU Devices
  - Negativ für CPU

- **Detection Size** (HxW)
  - `640,640` - standard
  - `1280,1280` - bessere Qualität (langsamer)

**Standard-Werte für Qualität + GPU:**
```json
{
  "insightface_model": "buffalo_l",
  "insightface_ctx": 0,
  "insightface_det_size": "1280,1280"
}
```

#### 🎬 Timelapse

- **Backend**
  - `auto` - best verfügbar
  - `onnx` - schnell wenn verfügbar
  - `superres` - SuperResolution
  - `local` - lokales Backend

- **SuperRes Modell**
  - ESPCN, FSRCNN, LapSRN

- **SuperRes Skalierung**
  - 2x (schnell)
  - 4x (beste Qualität, viel langsamer)

- **ONNX Face Size**
  - Größe für ONNX Gesichtserkennung
  - Standard: 256

## 💾 Speicherung & Fallback

**Hierarchie:**
1. **Datenbank** (neuer Standard) ← hier sind die Werte gespeichert
2. **ENV-Variablen** (Fallback für Legacy)
3. **Hardcoded Defaults** (letzte Option)

Beispiel: `FOTOS_YOLO_DEVICE=cpu` überschreibt DB-Wert

## 🗄️ Datenbank-Struktur

Tabelle: `admin_config`

```sql
CREATE TABLE admin_config (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
```

**Alle Schlüssel:**
- Index: `photo_roots`, `force_reindex`, `index_workers`, `near_duplicates`, `phash_threshold`, `rematch_workers`
- YOLO: `yolo_model`, `yolo_confidence`, `yolo_device`
- Personen: `person_backend`, `person_threshold`, `person_top_k`, `person_full_image_fallback`
- InsightFace: `insightface_model`, `insightface_ctx`, `insightface_det_size`
- Timelapse: `timelapse_ai_backend`, `timelapse_superres_model`, `timelapse_superres_name`, `timelapse_superres_scale`, `timelapse_face_onnx_model`, `timelapse_face_onnx_provider`, `timelapse_face_onnx_size`

## 🚀 Empfohlene Standard-Konfiguration

Für **maximale Qualität + GPU-Nutzung**:

```json
{
  "photo_roots": [],
  "force_reindex": false,
  "index_workers": 4,
  "near_duplicates": false,
  "phash_threshold": 6,
  "rematch_workers": 1,
  "yolo_model": "yolov8m.pt",
  "yolo_confidence": 0.15,
  "yolo_device": "0",
  "person_backend": "insightface",
  "person_threshold": 0.38,
  "person_top_k": 3,
  "person_full_image_fallback": true,
  "insightface_model": "buffalo_l",
  "insightface_ctx": 0,
  "insightface_det_size": "1280,1280",
  "timelapse_ai_backend": "auto",
  "timelapse_superres_model": "",
  "timelapse_superres_name": "espcn",
  "timelapse_superres_scale": 2,
  "timelapse_face_onnx_model": "",
  "timelapse_face_onnx_provider": "auto",
  "timelapse_face_onnx_size": 256
}
```

## ⚙️ Technische Details

### Initialisierung

Beim Start der Anwendung werden die Einstellungen aus der DB geladen:

```python
# In app/web/__init__.py
_initialize_settings(app.config["DB_PATH"])
```

Dies ruft auf:
- `initialize_yolo_settings(db_path)` → detectors/labels.py
- `initialize_person_settings(db_path)` → persons/service.py
- `initialize_insightface_settings(db_path)` → persons/embeddings.py

### Code-Integration

Alle Module lesen ihre Einstellungen global:

- `detectors/labels.py`: `_YOLO_MODEL_NAME`, `_YOLO_CONFIDENCE`, `_YOLO_DEVICE`
- `persons/service.py`: `_PERSON_THRESHOLD`, `_PERSON_TOP_K`, `_USE_FULL_IMAGE_FALLBACK`
- `persons/embeddings.py`: `_INSIGHTFACE_MODEL`, `_INSIGHTFACE_CTX`, `_INSIGHTFACE_DET_SIZE`

## 📝 Hinweise

- Änderungen in der Admin-UI werden **automatisch gespeichert**
- ENV-Variablen sind weiterhin für Fallback vorhanden
- Neue Einstellungen gelten nur für **zukünftige Verarbeitungen**
- Bereits indexierte Fotos müssen neu verarbeitet werden, um neue Settings zu nutzen


