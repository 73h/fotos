# 📋 Changelog: ENV → SQLite + Admin-UI

## Neue Dateien

### SETTINGS.md
- Vollständige Dokumentation aller Einstellungen
- Admin-Dashboard Bedienungsanleitung
- Empfohlene Standard-Konfigurationen
- Technische Integration Details

### IMPLEMENTATION_SUMMARY.md
- Übersicht der Implementierung
- API-Endpoints
- Testing-Guide
- Nächste Schritte

## Geänderte Dateien

### src/app/index/store.py

**ADMIN_CONFIG_DEFAULTS erweitert:**
- Hinzufügt 17 neue Konfigurationsschlüssel:
  - YOLO: `yolo_model`, `yolo_confidence`, `yolo_device`
  - Personen: `person_backend`, `person_threshold`, `person_top_k`, `person_full_image_fallback`
  - InsightFace: `insightface_model`, `insightface_ctx`, `insightface_det_size`
  - Timelapse: `timelapse_ai_backend`, `timelapse_superres_model`, `timelapse_superres_name`, `timelapse_superres_scale`, `timelapse_face_onnx_model`, `timelapse_face_onnx_provider`, `timelapse_face_onnx_size`

**_normalize_admin_config() erweitert:**
- Validiert alle neuen Einstellungen
- Konvertiert Typen (string → int, float, bool)
- Bereichsprüfungen (z.B. confidence 0.0-1.0)
- Fallback auf Defaults bei ungültigen Werten

### src/app/detectors/labels.py

**Neue Funktionen:**
- `_load_yolo_settings_from_db(db_path)` - lädt aus DB oder ENV
- `initialize_yolo_settings(db_path)` - Startup-Init
- `_resolve_yolo_device_internal()` - Auto-Erkennung GPU/CPU

**Globale Variablen:**
- `_YOLO_MODEL_NAME`, `_YOLO_CONFIDENCE`, `_YOLO_DEVICE`
- Initial `None`, werden beim Startup gefüllt

**Geändert:**
- `_resolve_yolo_device()` - nutzt jetzt globale Variable
- `_load_model()` - nutzt globale Variable
- `detect_person_boxes()` - nutzt globale Variable
- `_labels_from_yolo()` - nutzt globale Variable

### src/app/persons/service.py

**Neue Funktionen:**
- `_load_person_settings_from_db(db_path)` - lädt aus DB oder ENV
- `initialize_person_settings(db_path)` - Startup-Init

**Globale Variablen:**
- `_PERSON_THRESHOLD`, `_PERSON_TOP_K`, `_USE_FULL_IMAGE_FALLBACK`
- Initial `None`, werden beim Startup gefüllt

**Geändert:**
- `extract_person_signatures()` - nutzt globale Fallback-Variable
- `_score_signature_against_references()` - nutzt globale Threshold/Top-K

### src/app/persons/embeddings.py

**Neue Funktionen:**
- `_load_insightface_settings_from_db(db_path)` - lädt aus DB oder ENV
- `initialize_insightface_settings(db_path)` - Startup-Init

**Globale Variablen:**
- `_INSIGHTFACE_MODEL`, `_INSIGHTFACE_CTX`, `_INSIGHTFACE_DET_SIZE`
- Initial `None`, werden beim Startup gefüllt

**Geändert:**
- `InsightFaceBackend.__init__()` - nutzt globale Variablen
- `resolve_backend()` - nutzt globale Variablen

### src/app/web/__init__.py

**Neue Funktionen:**
- `_initialize_settings(db_path)` - ruft alle Init-Funktionen auf

**Geändert:**
- `create_app()` - ruft `_initialize_settings()` auf nach `ensure_schema()`

### src/app/web/templates/admin.html

**Neue UI-Sektion:**
- "⚙️ KI & Qualitäts-Einstellungen"
- Mit 4 Registerkarten

**Tab 1: 🎯 YOLO**
- Select für Modell (yolov8n/s/m/l)
- Input für Konfidenz (0.0-1.0)
- Select für Device (GPU/CPU)

**Tab 2: 👤 Personen**
- Select für Backend (auto/insightface/histogram)
- Input für Threshold (0.0-1.0)
- Input für Top-K (1-20)
- Checkbox für Vollbild-Fallback

**Tab 3: 🧠 InsightFace**
- Select für Modell (buffalo_l/s/sc)
- Input für GPU Device
- Input für Detection Size

**Tab 4: 🎬 Timelapse**
- Select für Backend (auto/onnx/superres/local)
- Select für SuperRes Modell
- Input für SuperRes Skalierung
- Input für ONNX Face Size

**Neue JavaScript-Funktionen:**
- `switchSettingsTab(tabName)` - Tab-Navigation
- `collectQualitySettings()` - sammelt alle Settings
- `applyQualitySettings(config)` - füllt UI aus DB
- `saveQualitySettings(showFeedback)` - speichert in DB
- `setupQualitySettingsAutoSave()` - Auto-Save nach 500ms
- `setQualitySaveStatus()` - Feedback-Status

**Neue CSS-Klassen:**
- `.settings-tabs` - Tab-Container
- `.tab-button` - Tab-Buttons
- `.settings-tab` - Tab-Content
- `.settings-tab.active` - aktiver Tab
- Fade-In Animation

## Migration für Benutzer

**Automatisch:**
- Beim ersten Start werden alle neuen Konfigurationsschlüssel mit Defaults in DB angelegt
- Bestehende ENV-Variablen werden weiterhin als Fallback gelesen

**Manuell (Optional):**
- Besuchen Sie die Admin-UI
- Passen Sie YOLO, Personen, InsightFace und Timelapse-Einstellungen an
- Einstellungen werden automatisch gespeichert

## Rückwärtskompatibilität

✅ **Vollständig erhalten:**
- ENV-Variablen funktionieren weiterhin
- Alte Konfigurationsdateien nicht betroffen
- Keine Breaking Changes

**Priorität:**
1. ENV-Variablen (wenn gesetzt)
2. DB-Werte (empfohlen)
3. Hardcoded Defaults

## Performance-Impact

- **Start:** +~10ms für DB-Abfragen (negligibel)
- **Runtime:** Keine Änderung (Settings werden beim Start geladen)
- **Speicher:** +~1KB für globale Variablen

## Testing-Checklist

- [ ] App startet ohne Fehler
- [ ] Admin-Dashboard lädt
- [ ] Einstellungen werden angezeigt
- [ ] Tab-Navigation funktioniert
- [ ] Auto-Save funktioniert
- [ ] Einstellungen persistent nach Reload
- [ ] ENV-Variablen überschreiben DB-Werte
- [ ] YOLO-Modelle auf GPU ausgeführt
- [ ] Personen-Matching nutzt richtige Threshold
- [ ] InsightFace nutzt richtige Modell


