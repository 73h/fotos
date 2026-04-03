# 📸 Fotos - Lokale KI-basierte Fotoverwaltung

![Vibe Coding](https://img.shields.io/badge/Coded%20with-100%25%20Vibe%20Coding-blueviolet?style=for-the-badge&logo=github)
![GitHub Copilot](https://img.shields.io/badge/Powered%20by-GitHub%20Copilot-000000?style=for-the-badge&logo=githubcopilot)

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3-lightblue.svg)](https://www.sqlite.org/)
[![YOLO](https://img.shields.io/badge/YOLO-v8-red.svg)](https://github.com/ultralytics/yolov8)

Lokale Foto-Suchmaschine mit YOLO-Objekterkennung, InsightFace-Gesichtserkennung, Album-Management und KI-Timelapse.

## ✨ About this project

- This project is **100% vibe coded** using **GitHub Copilot**.  
- No overthinking – just flow, intuition, and AI-assisted creativity 🚀

## ✨ Features

### 🔍 Intelligente Bildanalyse
- **YOLO v8** Objekterkennung für Szenen-Labels (Personen, Tiere, Objekte, Orte)
- **InsightFace** für hochpräzise Gesichtserkennung und Personenmatching
- **Histogram-Backend** als schneller Fallback
- Automatische Bildklassifizierung und Tagging

### 👤 Personenerkennung
- Gesichtserkennungs-Embeddings mit InsightFace (512-dimensional)
- Smile-Score Berechnung basierend auf Gesichtsmerkmalen
- Effizientes Personen-Matching mit konfigurierbarer Schwelle
- Histogram-basierte Person-Erkennungserkennung als Alternative

### 🔄 Duplikat-Verwaltung
- **Exakte Duplikate** via SHA-1 Hash
- **Nähere Duplikate** via Perceptual Hash (pHash)
- Konfigurierbare Ähnlichkeits-Schwellenwerte
- Effiziente Duplikat-Erkennung und Markierung

### 📅 Datum & EXIF-Verwaltung
- Automatische EXIF-Daten-Extraktion
- Flexibles Foto-Datierung
- Schnelle EXIF-Only Updates
- GPS-Daten-Unterstützung (mit Ortssuche)

### 🎬 Timelapse-AI (MVP)
- SuperResolution mit verschiedenen Modellen (ESPCN, FSRCNN, LapSRN)
- ONNX-beschleunigte Gesichtsverbesserung
- Automatische Video-Erstellung aus Bildreihen
- GPU-Unterstützung

### 🎨 Benutzeroberfläche
- **Moderne Web-UI** mit Flask
- **Responsive Design** für Desktop/Tablet/Mobile
- **Admin-Dashboard** mit Tabs für KI-Einstellungen
- **Live-Suche** mit Datum-, Personen- und Smile-Filtern
- **Album-Management** mit Duplikat-Erkennung
- **Aging-Best-of Album Builder** pro Person (GPU-first mit optional strikt ohne CPU-Fallback)
- Qualitätsregler fuer Aging-Auswahl (mehr Vielfalt bis max Qualitaet)
- Optional direkt in bestehendes Album schreiben
- Optional nach Album-Build automatisch Timelapse-Job starten
- **Job-Verwaltung** für Batch-Operationen

### ⚙️ Konfiguration
- **SQLite-basierte Settings** (nicht ENV-Variablen)
- **Admin-UI** für alle Einstellungen
- **GPU-Optimierung** mit Auto-Erkennung
- **Worker-Threads** konfigurierbar

---

## 🚀 Quick Start

### Anforderungen
- Python 3.11+
- GPU (NVIDIA mit CUDA, optional für CPU-Fallback)
- 8 GB+ RAM (16+ empfohlen für GPU)

### Installation

```bash
# Repository klonen
git clone https://github.com/yourusername/fotos.git
cd fotos

# Virtuelle Umgebung
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# oder
.venv\Scripts\activate     # Windows

# Abhängigkeiten
pip install -r requirements.txt
pip install -r requirements-face.txt  # Für InsightFace

# GPU Setup (optional)
./scripts/setup_gpu.ps1  # Windows PowerShell
```

### Anwendung starten

```bash
# Web-Interface
python src/main_web.py
# → http://localhost:5000

# CLI-Tool
python src/main.py --help
```

### Erste Schritte
1. Admin-Dashboard: `http://localhost:5000/admin`
2. Foto-Pfade konfigurieren
3. "Full Indexierung" starten
4. Fotos durchsuchen

---


## 🎛️ Konfiguration

### Admin-Dashboard
Alle Einstellungen werden im Admin-Dashboard konfiguriert:

```
http://localhost:5000/admin
```

**Registerkarten:**
- 🎯 **YOLO** - Objekterkennung (Modell, Konfidenz, Device)
- 👤 **Personen** - Gesichtserkennung (Backend, Threshold, Top-K)
- 🧠 **InsightFace** - Face-Embedding (Modell, GPU, Detection-Size)
- 🎬 **Timelapse** - Video-Generierung (Modelle, Provider)

### Standard-Werte (optimiert)
```
YOLO: yolov8m.pt, Device: GPU(0), Confidence: 0.15
Personen: InsightFace, Threshold: 0.38, Top-K: 3
InsightFace: buffalo_l, GPU, 1280x1280 Detection
```

---

## 🔍 Verwendungsbeispiele

### Fotosuche
```
Einfache Suche: "strand"
Nach Personen: "person:Maria"
Nach Datum: "month:06 year:2023"
Mit Lächeln: "smile:0.8"
```

### Admin-Operationen
```
Full Indexierung    - Scannt & verarbeitet alle Fotos
EXIF Update         - Aktualisiert nur Metadaten
Rematch Personen    - Neuberechnung mit neuen Settings
Objekt-Erkennung    - Detaillierte YOLO-Analyse pro Bild
```

### Feine Labels in der Indexierung speichern

Du kannst bei der Standard-Indexierung feine YOLO-Klassenlabels mitschreiben:
```bash
python src/main.py index --root "D:/Fotos" --include-fine-labels
```

Beim späteren Re-Index hast du jetzt zwei Modi:

```bash
# Standard: feine Labels werden pro verarbeitetem Bild neu berechnet
python src/main.py index --root "D:/Fotos" --include-fine-labels

# Merge: vorhandene yolo:* Labels bleiben erhalten und neue Treffer kommen dazu
python src/main.py index --root "D:/Fotos" --include-fine-labels --merge-fine-labels
```

Hinweis: `--merge-fine-labels` wirkt nur zusammen mit `--include-fine-labels`.

Die feinen Labels werden mit Präfix `yolo:` gespeichert (z. B. `yolo:cat`, `yolo:chair`) und ermöglichen später:
- Nach einzelnen Klassen suchen: `cat`, `dog`, `chair`, `car`
- Kombiniert mit anderen Filtern: `cat person:Maria`

### Separate YOLO-Tier-/Objekt-Erkennung
```bash
# CLI-Tool: Losgelöst ohne DB-Indexierung
python src/main.py detect-objects "D:/Fotos/testbild.jpg"
python src/main.py detect-objects "D:/Fotos/urlaub" --model yolov8m.pt --confidence 0.2

# Nur bestimmte Klassen betrachten
python src/main.py detect-objects "D:/Fotos/haustiere" --labels cat,dog,bird
python src/main.py detect-objects "D:/Fotos/wohnung" --labels chair,couch,tv,cell phone

# JSON-Bericht schreiben
python src/main.py detect-objects "D:/Fotos" --json --output data/cache/detections.json
```

Die Ausgabe enthält pro Bild:
- die feinen YOLO-Klassen (z. B. `cat`, `dog`, `chair`, `car`)
- eine grobe Art (`animal`, `object`, optional `person`)
- eine Gruppe (`pet`, `vehicle`, `furniture`, `electronics`, ...)
- Confidence und Bounding Box pro Treffer

### Web-UI: Objekt-Erkennung im Admin-Dashboard

Im Admin-Dashboard (`http://localhost:5000/admin`) kannst du jetzt:
- **Objekt-Erkennungs-Job starten** – alle Bilder analysieren
- Parameter einstellen:
  - YOLO-Modell (z. B. `yolov8m.pt`)
  - Konfidenzschwelle (0..1)
  - Device (`cpu`, `0` für GPU, etc.)
  - Klassenfilter (kommagetrennt, z. B. `cat,dog,bird`)
  - Optional: `person`-Klasse einschließen
- Für die **Indexierung** kannst du zusätzlich setzen:
  - **Feine YOLO-Labels speichern**
  - **Feine Labels beim Re-Index mergen** (alte `yolo:*` behalten)
- Im YOLO-Tab kannst du eine **Fine-Label-Liste (CSV)** pflegen (`yolo_label_allowlist_csv`).
  Nur diese Klassen werden als `yolo:*` gespeichert.
- **Echtzeit-Progress**: Anzahl verarbeiteter Bilder und Treffer
- **Job-Verwaltung**: Status-Überwachung, optional Abbruch

### Fehlende Fine-Labels nachziehen (ohne Full Re-Index)

Im Admin-Bereich gibt es die Operation **"Fine-Labels nachziehen"**.
Sie prüft bereits indexierte Bilder und ergänzt nur dort `yolo:*`, wo noch keine Fine-Labels vorhanden sind.

Hinweis: Die Standard-Modelle `yolov8n.pt` / `yolov8m.pt` erkennen die üblichen COCO-Klassen.
Für echte Arten auf Spezies-Niveau (z. B. Tiger, Fuchs, Reh, verschiedene Vogelarten) brauchst du ein passendes Custom-Modell oder Fine-Tuning.

### Album-Management
```
Alben erstellen
Fotos hinzufügen/entfernen
Duplikate erkennen
Cover-Foto setzen
Alben exportieren (ZIP)
```

---

## 🏗️ Architektur

```
fotos/
├── src/
│   ├── app/
│   │   ├── index/          (Foto-Index, DB)
│   │   ├── detectors/      (YOLO, Labels)
│   │   ├── persons/        (Gesichtserkennung)
│   │   ├── albums/         (Album-Management)
│   │   ├── search/         (Suche-Engine)
│   │   └── web/            (Flask UI, Admin)
│   ├── main.py             (CLI)
│   └── main_web.py         (Web-Server)
└── data/
    └── photo_index.db      (SQLite)
```

### Technologie-Stack
- **Backend:** Python Flask, SQLite3
- **Objekterkennung:** YOLO v8 (Ultralytics)
- **Gesichtserkennung:** InsightFace
- **GPU:** CUDA 11.8+, cuDNN 8.x
- **Frontend:** HTML/CSS/JavaScript (responsive)

---

## 📊 Performance

### Indizierung
- **GPU:** ~200-500 Fotos/Minute (je nach Modell)
- **CPU:** ~20-50 Fotos/Minute
- **Duplikat-Erkennung:** ~1000+ Fotos/Sekunde

### Suche
- **Text-Suche:** <100ms (1000+ Fotos)
- **Personen-Matching:** ~50ms pro Person
- **Album-Filter:** <50ms

### Speicher
- **Durchschnitt:** ~1-2 KB pro Foto in DB
- **Embeddings:** 512 Dimension × 4 Byte = 2 KB pro Face

---

## 🔒 Sicherheit & Datenschutz

- ✅ Lokale SQLite-Datenbank (keine Cloud)
- ✅ Keine Foto-Upload zu externen Services
- ✅ Rückwärtskompatibilität mit ENV-Variablen
- ✅ Admin-Panel ohne externen Auth (lokal nur)

---

## 🐛 Troubleshooting

### GPU-Probleme
```bash
python src/main.py doctor  # Diagnose aller Komponenten
```

### Performance
- Kleinere Worker-Threads wenn CPU überlastet
- GPU-Device überprüfen (CUDA verfügbar?)
- YOLO-Modell verkleinern (yolov8n statt yolov8m)

### Fehler
Siehe GitHub Issues für bekannte Probleme.

---

## 🤝 Beitragen

Contributions sind willkommen! Bitte:
1. Fork das Repository
2. Feature-Branch erstellen (`git checkout -b feature/amazing`)
3. Commits mit guten Messages
4. Pull Request erstellen

---

## 🎉 Credits

Gebaut mit:
- [YOLO v8](https://github.com/ultralytics/yolov8) - Objekterkennung
- [InsightFace](https://github.com/deepinsight/insightface) - Gesichtserkennung
- [Flask](https://flask.palletsprojects.com/) - Web-Framework
- [PyTorch](https://pytorch.org/) - Deep Learning

---

**Zuletzt aktualisiert:** 2026-04-02  
**Version:** 1.0.0


