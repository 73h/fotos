# 🎉 Implementierung: ENV → SQLite + Admin-UI

## ✅ Was wurde implementiert

### 1. **Datenbankschema erweitert** (src/app/index/store.py)
- 17 neue Konfigurationsschlüssel in `ADMIN_CONFIG_DEFAULTS`
- Normalisierungsfunktion erweitert für Validierung aller neuen Settings
- Standard-Werte für maximale Qualität + GPU-Nutzung

### 2. **YOLO-Einstellungen** (src/app/detectors/labels.py)
- `_load_yolo_settings_from_db()` - lädt aus DB + ENV-Fallback
- `initialize_yolo_settings()` - Startup-Initialisierung
- Globale Variablen: `_YOLO_MODEL_NAME`, `_YOLO_CONFIDENCE`, `_YOLO_DEVICE`

### 3. **Personen-Matching** (src/app/persons/service.py)
- `_load_person_settings_from_db()` - lädt aus DB + ENV-Fallback
- `initialize_person_settings()` - Startup-Initialisierung
- Globale Variablen: `_PERSON_THRESHOLD`, `_PERSON_TOP_K`, `_USE_FULL_IMAGE_FALLBACK`

### 4. **InsightFace** (src/app/persons/embeddings.py)
- `_load_insightface_settings_from_db()` - lädt aus DB + ENV-Fallback
- `initialize_insightface_settings()` - Startup-Initialisierung
- Globale Variablen: `_INSIGHTFACE_MODEL`, `_INSIGHTFACE_CTX`, `_INSIGHTFACE_DET_SIZE`

### 5. **Admin-UI erweitert** (src/app/web/templates/admin.html)
- Neue Sektion "⚙️ KI & Qualitäts-Einstellungen"
- 4 Registerkarten:
  - 🎯 YOLO (Modell, Konfidenz, Device)
  - 👤 Personen (Backend, Threshold, Top-K, Fallback)
  - 🧠 InsightFace (Modell, GPU Device, Detection Size)
  - 🎬 Timelapse (Backend, SuperRes, ONNX)
- Auto-Save funktionalität
- Tab-Navigation

### 6. **Web-App Initialisierung** (src/app/web/__init__.py)
- `_initialize_settings(db_path)` - lädt alle Settings beim Start
- Ruft alle Modul-Initialisierungsfunktionen auf

## 🔄 Ablauf beim Start

```
1. App startet (main_web.py)
   ↓
2. create_app() wird aufgerufen
   ↓
3. ensure_schema() → DB-Tabellen werden erstellt
   ↓
4. _initialize_settings() 
   ├─ initialize_yolo_settings() → liest DB/ENV
   ├─ initialize_person_settings() → liest DB/ENV
   └─ initialize_insightface_settings() → liest DB/ENV
   ↓
5. Globale Variablen sind gefüllt
   ↓
6. App läuft → alle Funktionen nutzen Settings aus DB
```

## 📊 Daten-Hierarchie

```
Priorität (von hoch zu niedrig):
1. Umgebungsvariablen (ENV) - für Rückwärtskompatibilität
2. Datenbank (SQLite) - neuer Standard ⭐
3. Hardcoded Defaults - letzte Option
```

## 🎯 Standard-Werte (Qualität + GPU)

| Setting | Wert | Begründung |
|---------|------|-----------|
| `yolo_model` | yolov8m.pt | Beste Balance Qualität/Geschwindigkeit |
| `yolo_confidence` | 0.15 | Niedrig = mehr Treffer |
| `yolo_device` | 0 (GPU) | GPU 100x schneller als CPU |
| `person_backend` | insightface | Deutlich bessere Qualität |
| `person_threshold` | 0.38 | Standard für gutes Matching |
| `insightface_model` | buffalo_l | Beste Genauigkeit |
| `insightface_ctx` | 0 (GPU) | GPU-Beschleunigung |
| `insightface_det_size` | 1280,1280 | Höhere Auflösung = bessere Erkennung |

## 🔗 API Endpoints

```
GET  /api/admin/config
  → Liest aktuelle Konfiguration aus DB

POST /api/admin/config
  → Speichert neue Konfiguration in DB
```

Payload-Beispiel:
```json
{
  "yolo_model": "yolov8m.pt",
  "yolo_confidence": 0.15,
  "yolo_device": "0",
  "person_backend": "insightface",
  ...
}
```

## 🧪 Testing

### Auto-Save testen
1. Admin-Dashboard öffnen
2. YOLO-Konfidenz ändern
3. Sollte automatisch nach 500ms speichern
4. Seite neu laden → Wert ist noch da

### Verschiedene Backends testen
1. Person-Backend auf "histogram" setzen
2. Rematch durchführen
3. Sollte mit Histogram-Backend arbeiten

### GPU testen
1. YOLO Device auf "0" setzen
2. Objekte sollten schnell erkannt werden
3. Device auf "cpu" setzen
4. Sollte langsamer sein

## 📝 Notizen für Benutzer

- ✅ Alle Einstellungen sind jetzt zentral in der Admin-UI
- ✅ Keine ENV-Variablen mehr nötig (aber weiterhin unterstützt)
- ✅ Einstellungen werden in SQLite persistent gespeichert
- ✅ Auto-Save verhindert Datenverlust
- ✅ Standard-Werte für maximale Qualität voreingestellt
- ✅ GPU wird automatisch erkannt und genutzt

## 🚀 Nächste Schritte (Optional)

- Validierung von Dateipfaden für Timelapse-Modelle
- Export/Import der Konfiguration (JSON-Download)
- Konfigurationsprofile (z.B. "Gaming PC", "Laptop", "Server")
- Monitoring der aktuellen Einstellungen im Logs


