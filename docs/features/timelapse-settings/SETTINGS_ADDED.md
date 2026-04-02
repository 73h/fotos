# ✅ Timelapse-Modelle in Admin-UI hinzugefügt

**Datum:** 2026-04-02  
**Status:** ✅ ERLEDIGT

## Was wurde hinzugefügt

Die fehlenden Einträge aus `.env.timelapse` sind nun in der Admin-UI verfügbar:

### 🎬 Timelapse-Tab - SuperResolution

```html
<!-- Neue Felder -->
<input id="timelapse-superres-model-input" placeholder="z.B. D:\models\ESPCN_x2.pb" />
```

**Settings:**
- ✅ `timelapse_superres_model` - Dateipfad zur SuperRes-Modell-Datei (.pb)
- ✅ `timelapse_superres_name` - Modelltyp (espcn/fsrcnn/lapsrn)
- ✅ `timelapse_superres_scale` - Skalierungsfaktor (1-4)

### 🧠 Timelapse-Tab - ONNX Face Enhancer

```html
<!-- Neue Felder -->
<input id="timelapse-face-onnx-model-input" placeholder="z.B. D:\models\face_enhancer.onnx" />
<select id="timelapse-face-onnx-provider-input">
  <option value="auto">auto</option>
  <option value="cuda">CUDA (GPU)</option>
  <option value="cpu">CPU</option>
</select>
```

**Settings:**
- ✅ `timelapse_face_onnx_model` - Dateipfad zur ONNX-Modell-Datei (.onnx)
- ✅ `timelapse_face_onnx_provider` - Execution Provider (auto/cuda/cpu)
- ✅ `timelapse_face_onnx_size` - Face Size (256 standard)

## Änderungen in `admin.html`

### Neue UI-Elemente (Timelapse-Tab)

```html
<h3>📊 SuperResolution</h3>
├─ Modell-Datei (Input)
├─ Modell (Select: espcn/fsrcnn/lapsrn)
└─ Skalierung (Input: 1-4)

<h3>🧠 ONNX Face Enhancer</h3>
├─ ONNX Model-Datei (Input)
├─ ONNX Provider (Select: auto/cuda/cpu)
└─ Face Size (Input: 32-512)
```

### JavaScript Updates

**collectQualitySettings()** - neu hinzugefügt:
- `timelapse_superres_model` ✅
- `timelapse_face_onnx_model` ✅
- `timelapse_face_onnx_provider` ✅

**applyQualitySettings()** - neu hinzugefügt:
- Lädt `timelapse_superres_model` ✅
- Lädt `timelapse_face_onnx_model` ✅
- Lädt `timelapse_face_onnx_provider` ✅

**setupQualitySettingsAutoSave()** - neu hinzugefügt:
- `timelapse-superres-model-input` ✅
- `timelapse-face-onnx-model-input` ✅
- `timelapse-face-onnx-provider-input` ✅

## Migration von `.env.timelapse`

### Vorher (ENV-Variablen):
```powershell
$env:FOTOS_TIMELAPSE_SUPERRES_MODEL="D:\models\ESPCN_x2.pb"
$env:FOTOS_TIMELAPSE_SUPERRES_NAME="espcn"
$env:FOTOS_TIMELAPSE_SUPERRES_SCALE="2"
$env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL="D:\models\face_enhancer.onnx"
$env:FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER="auto"
$env:FOTOS_TIMELAPSE_FACE_ONNX_SIZE="256"
```

### Nachher (SQLite DB über Admin-UI):
```
Timelapse-Tab öffnen
  ↓
📊 SuperResolution Sektion:
  - Model-Datei: D:\models\ESPCN_x2.pb
  - Modell: espcn
  - Skalierung: 2
  ↓
🧠 ONNX Face Enhancer Sektion:
  - ONNX Model-Datei: D:\models\face_enhancer.onnx
  - ONNX Provider: auto
  - Face Size: 256
```

## Admin-UI Übersicht

### Timelapse-Tab Struktur

```
⚙️ KI & Qualitäts-Einstellungen
    ├─ 🎯 YOLO
    ├─ 👤 Personen
    ├─ 🧠 InsightFace
    └─ 🎬 Timelapse ← HIER!
         ├─ Backend (Select)
         ├─ 📊 SuperResolution
         │  ├─ Modell (Select)
         │  ├─ Model-Datei (Input)  ← NEU! ✅
         │  └─ Skalierung (Input)
         ├─ 🧠 ONNX Face Enhancer
         │  ├─ ONNX Model-Datei (Input)  ← NEU! ✅
         │  ├─ ONNX Provider (Select)      ← NEU! ✅
         │  └─ Face Size (Input)
```

## Features

✅ **Auto-Save**
- Änderungen werden automatisch nach 500ms gespeichert
- Kein manuelles Speichern nötig

✅ **Validierung**
- Dateipfade können beliebig sein (keine Validierung)
- Skalierung: 1-4
- Face Size: 32-512

✅ **Fallback**
- Wenn nicht gesetzt: "" (leer)
- DB-Wert oder ENV-Variable überschreiben

✅ **Persistent**
- Einstellungen werden in SQLite gespeichert
- Nach Reload noch vorhanden

## Testing

### Schritt 1: Admin-UI öffnen
```
http://localhost:5000/admin
```

### Schritt 2: Timelapse-Tab öffnen
```
Klick auf "🎬 Timelapse" Button
```

### Schritt 3: Werte eintragen
```
SuperResolution:
  Model-Datei: D:\models\ESPCN_x2.pb
  Modell: espcn
  Skalierung: 2

ONNX Face Enhancer:
  ONNX Model-Datei: D:\models\face_enhancer.onnx
  ONNX Provider: auto
  Face Size: 256
```

### Schritt 4: Auto-Save beobachten
```
"Qualitäts-Einstellungen gespeichert." sollte erscheinen
```

### Schritt 5: Persistenz testen
```
Seite neuladen (F5)
→ Werte sollten noch da sein ✅
```

## Code-Changes

**Datei:** `src/app/web/templates/admin.html`

| Änderung | Zeilen | Details |
|---|---|---|
| Neue HTML-Felder | +25 | SuperRes & ONNX File-Inputs |
| collectQualitySettings() | +2 | 2 neue Keys sammeln |
| applyQualitySettings() | +3 | 3 neue Keys in UI füllen |
| setupQualitySettingsAutoSave() | +3 | 3 neue IDs für Auto-Save |

**Total:** +33 Zeilen HTML/JS

## Kompatibilität

✅ **Rückwärtskompatibel**
- `.env.timelapse` funktioniert weiterhin
- ENV-Variablen werden als Fallback gelesen
- Keine Breaking Changes

✅ **Datenbank**
- Keys waren bereits in ADMIN_CONFIG_DEFAULTS vorhanden
- Nur UI fehlte
- Keine DB-Migration nötig

✅ **Fallback-Logik**
```
1. Prüfe ENV: FOTOS_TIMELAPSE_*
2. Fallback auf DB-Werte
3. Fallback auf Defaults ("")
```

## Zusammenfassung

| Setting | Vorher | Nachher |
|---|---|---|
| superres_model | ENV .env.timelapse | ✅ Admin-UI Input |
| superres_name | ENV .env.timelapse | ✅ Admin-UI Select |
| superres_scale | ENV .env.timelapse | ✅ Admin-UI Input |
| face_onnx_model | ENV .env.timelapse | ✅ Admin-UI Input |
| face_onnx_provider | ENV .env.timelapse | ✅ Admin-UI Select |
| face_onnx_size | ENV .env.timelapse | ✅ Admin-UI Input |

**Status:** 🟢 Alle Timelapse-Settings sind jetzt in der Admin-UI verfügbar!


