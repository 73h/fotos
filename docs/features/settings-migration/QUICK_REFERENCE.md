# ⚡ Quick Reference: Settings-System

## 🎯 Schnelle Überblick

```python
# ❌ ALT: ENV-Variablen
os.getenv("FOTOS_YOLO_DEVICE", "auto")
os.getenv("FOTOS_PERSON_THRESHOLD", "0.38")

# ✅ NEU: Aus Datenbank
# (automatisch beim Start geladen)
_YOLO_DEVICE  # globale Variable in labels.py
_PERSON_THRESHOLD  # globale Variable in service.py
```

## 📁 Wichtige Dateien

| Datei | Änderungen | Funktion |
|---|---|---|
| src/app/index/store.py | +90 LOC | DB-Schema, Normalisierung |
| src/app/detectors/labels.py | +40 LOC | YOLO-Settings-Laden |
| src/app/persons/service.py | +45 LOC | Personen-Settings-Laden |
| src/app/persons/embeddings.py | +50 LOC | InsightFace-Settings-Laden |
| src/app/web/__init__.py | +25 LOC | Startup-Initialisierung |
| src/app/web/templates/admin.html | +200 LOC | Admin-UI mit Tabs |

## 🔧 Settings hinzufügen

**Schritt 1: DB-Defaults**
```python
# src/app/index/store.py - ADMIN_CONFIG_DEFAULTS
"my_setting_name": "default_value",
```

**Schritt 2: Normalisierung**
```python
# src/app/index/store.py - _normalize_admin_config()
my_value = raw_config.get("my_setting_name", normalized["my_setting_name"])
if isinstance(my_value, str):
    normalized["my_setting_name"] = my_value.strip()
```

**Schritt 3: Modul-Laden**
```python
# src/app/detectors/labels.py (oder personen, embeddings)
def _load_settings_from_db(db_path: Path | None = None):
    ...
    if db_path and db_path.exists():
        config = get_admin_config(db_path)
        my_value = config.get("my_setting_name", default)
    ...
```

**Schritt 4: UI**
```html
<!-- src/app/web/templates/admin.html -->
<div id="mytab-tab" class="settings-tab">
  <div class="form-group">
    <label for="my-setting-input">My Setting:</label>
    <input type="text" id="my-setting-input" class="form-control" />
  </div>
</div>
```

## 📡 API Calls

```javascript
// Settings laden
fetch('/api/admin/config')
  .then(r => r.json())
  .then(config => console.log(config))

// Settings speichern
fetch('/api/admin/config', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    yolo_model: "yolov8m.pt",
    person_threshold: 0.35
  })
})
```

## 🐛 Debugging

```python
# Settings anzeigen
from app.index.store import get_admin_config
config = get_admin_config(Path("data/photo_index.db"))
print(config)
```

```python
# Globale Variablen prüfen
from app.detectors.labels import _YOLO_DEVICE
print(f"YOLO Device: {_YOLO_DEVICE}")
```

## 🧪 Test-Szenarien

### Scenario 1: DB-Wert nutzen
1. DB hat `yolo_device=0`
2. Kein ENV: `FOTOS_YOLO_DEVICE`
3. → Nutzt DB-Wert: `0`

### Scenario 2: ENV überschreibt
1. DB hat `yolo_device=0`
2. ENV: `FOTOS_YOLO_DEVICE=cpu`
3. → Nutzt ENV: `cpu`

### Scenario 3: Default-Fallback
1. DB hat `yolo_device=xxx` (ungültig)
2. Kein ENV
3. → Nutzt Default: `0`

## 💡 Best Practices

✅ **Richtig:**
```python
# Modul-Start: Initialisieren
initialize_yolo_settings(db_path)

# Runtime: Globale Variable nutzen
device = _YOLO_DEVICE
```

❌ **Falsch:**
```python
# Runtime: Jedes Mal von ENV lesen
device = os.getenv("FOTOS_YOLO_DEVICE", "auto")

# Nicht: Globale Variable ändern
_YOLO_DEVICE = "cpu"  # Funktioniert nicht richtig!
```

## 📚 Konfigurationsschlüssel

```python
ADMIN_CONFIG_DEFAULTS = {
    # Index
    "photo_roots": [],
    "force_reindex": False,
    "index_workers": 1,
    "near_duplicates": False,
    "phash_threshold": 6,
    "rematch_workers": 1,
    # YOLO
    "yolo_model": "yolov8n.pt",
    "yolo_confidence": 0.25,
    "yolo_device": "0",
    # Personen
    "person_backend": "insightface",
    "person_threshold": 0.38,
    "person_top_k": 3,
    "person_full_image_fallback": True,
    # InsightFace
    "insightface_model": "buffalo_l",
    "insightface_ctx": 0,
    "insightface_det_size": "640,640",
    # Timelapse
    "timelapse_ai_backend": "auto",
    "timelapse_superres_model": "",
    "timelapse_superres_name": "espcn",
    "timelapse_superres_scale": 2,
    "timelapse_face_onnx_model": "",
    "timelapse_face_onnx_provider": "auto",
    "timelapse_face_onnx_size": 256,
}
```

## 🔄 Initialisierungsreihenfolge

```
Beim App-Start:
1. app = create_app(config)           # main_web.py
2. ensure_schema(db_path)             # web/__init__.py
3. _initialize_settings(db_path)      # web/__init__.py
   ├─ initialize_yolo_settings()      # detectors/labels.py
   ├─ initialize_person_settings()    # persons/service.py
   └─ initialize_insightface_settings()  # persons/embeddings.py
```

## 🎨 UI-Komponenten

```html
<!-- Tab-Navigation -->
<button class="tab-button active" onclick="switchSettingsTab('yolo')">
  🎯 YOLO
</button>

<!-- Tab-Inhalt -->
<div id="yolo-tab" class="settings-tab active">
  <div class="form-group">
    <label for="yolo-model-input">Modell:</label>
    <select id="yolo-model-input" class="form-control">
      <option value="yolov8n.pt">yolov8n</option>
    </select>
  </div>
</div>

<!-- Auto-Save Handler -->
<script>
el.addEventListener('change', () => {
  setTimeout(() => saveQualitySettings(false), 500)
})
</script>
```

## 🔗 Module Abhängigkeiten

```
web/__init__.py
├─ detectors/labels.py (initialize_yolo_settings)
├─ persons/service.py (initialize_person_settings)
├─ persons/embeddings.py (initialize_insightface_settings)
└─ index/store.py (get_admin_config)

web/routes.py
├─ api_admin_get_config (→ get_admin_config)
├─ api_admin_save_config (→ save_admin_config)
└─ admin.html (frontend)
```

## 📦 Abhängigkeits-Tree

```
main_web.py
└─ create_app()
   └─ _initialize_settings()
      ├─ initialize_yolo_settings()
      │  └─ get_admin_config()
      ├─ initialize_person_settings()
      │  └─ get_admin_config()
      └─ initialize_insightface_settings()
         └─ get_admin_config()
```

---

**Version:** 1.0 | **Datum:** 2026-04-02 | **Status:** Production Ready ✅


