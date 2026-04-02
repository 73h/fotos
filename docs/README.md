# 📚 Fotos-Dokumentation

Willkommen in der Dokumentation des **Fotos-Projekts**! Diese Dokumentation ist in mehrere Bereiche organisiert.

## 🗂️ Struktur

```
docs/
├── features/           📦 Umgesetzte Features
│   ├── settings-migration/    (ENV → SQLite + Admin-UI)
│   ├── timelapse-settings/    (Timelapse Konfiguration)
│   └── admin-redesign/        (Admin-UI Design)
├── setup/             🔧 Setup & Installation
│   ├── GPU_SETUP.md
│   └── GPU_QUICK_REFERENCE.md
└── general/           📋 Allgemeine Dokumentation
    ├── PROJECT_README.md
    ├── SETTINGS.md
    └── LOCAL_README.md
```

## 🚀 Quick Start

### Ich bin neu im Projekt
→ Lesen Sie [`docs/general/PROJECT_README.md`](general/PROJECT_README.md)

### Ich möchte die Einstellungen verstehen
→ Lesen Sie [`docs/general/SETTINGS.md`](general/SETTINGS.md)

### Ich möchte die GPU konfigurieren
→ Lesen Sie [`docs/setup/GPU_SETUP.md`](setup/GPU_SETUP.md)

### Ich interessiere mich für Features
→ Lesen Sie [`docs/features/`](features/README.md)

---

## 📦 Features

### 🔧 Settings Migration (ENV → SQLite)
- **Datei:** [`docs/features/settings-migration/00_START_HERE.md`](features/settings-migration/00_START_HERE.md)
- **Inhalt:**
  - ✅ Alle ENV-Variablen in SQLite speichern
  - ✅ Admin-Dashboard mit Tabs
  - ✅ Auto-Save Funktionalität
  - ✅ Rückwärtskompatibilität

### 🎬 Timelapse Settings
- **Datei:** [`docs/features/timelapse-settings/SETTINGS_ADDED.md`](features/timelapse-settings/SETTINGS_ADDED.md)
- **Inhalt:**
  - ✅ SuperResolution Model-Pfade
  - ✅ ONNX Face Enhancer Config
  - ✅ Provider-Auswahl (CUDA/CPU)

### 🎨 Admin UI Redesign
- **Datei:** [`docs/features/admin-redesign/DESIGN.md`](features/admin-redesign/DESIGN.md)
- **Inhalt:**
  - ✅ Moderne Farbpalette (Blau-Grün)
  - ✅ Animationen & Hover-Effekte
  - ✅ Responsive Design
  - ✅ CSS Variables

---

## 🔧 Setup & Installation

### GPU Konfiguration
- [`GPU_SETUP.md`](setup/GPU_SETUP.md) - Vollständige GPU-Setup Anleitung
- [`GPU_QUICK_REFERENCE.md`](setup/GPU_QUICK_REFERENCE.md) - Schnelle Referenz

---

## 📋 Allgemeine Dokumentation

- [`PROJECT_README.md`](general/PROJECT_README.md) - Projekt-Übersicht
- [`SETTINGS.md`](general/SETTINGS.md) - Einstellungen erklärt
- [`LOCAL_README.md`](general/LOCAL_README.md) - Lokale Konfiguration

---

## 🎯 Nach Benutzerrolle

### 👤 Benutzer / Admin
- [`docs/general/SETTINGS.md`](general/SETTINGS.md)
- [`docs/features/admin-redesign/DESIGN.md`](features/admin-redesign/DESIGN.md)

### 👨‍💻 Entwickler
- [`docs/features/settings-migration/QUICK_REFERENCE.md`](features/settings-migration/QUICK_REFERENCE.md)
- [`docs/features/settings-migration/IMPLEMENTATION.md`](features/settings-migration/IMPLEMENTATION.md)

### 🔧 DevOps / Infrastruktur
- [`docs/setup/GPU_SETUP.md`](setup/GPU_SETUP.md)
- [`docs/features/settings-migration/COMPLETION_REPORT.md`](features/settings-migration/COMPLETION_REPORT.md)

### 📊 Projekt-Manager
- [`docs/features/settings-migration/COMPLETION_REPORT.md`](features/settings-migration/COMPLETION_REPORT.md)
- [`docs/features/`](features/) (Feature Übersichten)

---

## 🗂️ Dateibaum

```
docs/
├── README.md                                     (Sie sind hier!)
│
├── features/
│   ├── README.md                               (Features-Übersicht)
│   │
│   ├── settings-migration/
│   │   ├── 00_START_HERE.md                    (🎯 Beginnen Sie hier!)
│   │   ├── IMPLEMENTATION.md                   (Technische Details)
│   │   ├── QUICK_REFERENCE.md                  (Schnelle Referenz)
│   │   ├── CHANGELOG.md                        (Was sich ändert)
│   │   └── COMPLETION_REPORT.md                (Projektbericht)
│   │
│   ├── timelapse-settings/
│   │   └── SETTINGS_ADDED.md                   (Timelapse Konfiguration)
│   │
│   └── admin-redesign/
│       └── DESIGN.md                           (Admin-UI Design)
│
├── setup/
│   ├── GPU_SETUP.md                            (GPU Konfiguration)
│   └── GPU_QUICK_REFERENCE.md                  (GPU Schnellstart)
│
└── general/
    ├── PROJECT_README.md                       (Projekt-Übersicht)
    ├── SETTINGS.md                             (Einstellungen)
    └── LOCAL_README.md                         (Lokale Konfiguration)
```

---

## 🔗 Navigation

- [Features](features/README.md)
- [Setup](setup/README.md)
- [Einstellungen](general/SETTINGS.md)
- [GPU Konfiguration](setup/GPU_SETUP.md)

---

## 🚨 Wichtige Links

- **Start Settings-Migration:** [00_START_HERE](features/settings-migration/00_START_HERE.md)
- **Admin-UI Anleitung:** [SETTINGS.md](general/SETTINGS.md)
- **GPU Setup:** [GPU_SETUP.md](setup/GPU_SETUP.md)

---

## 📝 Hinweise

Diese Dokumentation wird kontinuierlich aktualisiert. Neue Features werden in die entsprechenden Ordner unter `docs/features/` hinzugefügt.

**Struktur für neue Features:**
```
docs/features/[feature-name]/
├── README.md           (Übersicht)
├── 00_START_HERE.md    (Einstieg)
├── IMPLEMENTATION.md   (Technisch)
└── CHANGELOG.md        (Änderungen)
```

---

**Zuletzt aktualisiert:** 2026-04-02  
**Version:** 1.0


