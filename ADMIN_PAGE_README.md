# Admin-Seite und Job-Management

## Übersicht

Die Admin-Seite in `fotos` ermöglicht es dir, Index-, EXIF- und Rematch-Operationen direkt über die Web-UI zu verwalten. Du kannst:

- **Foto-Pfade konfigurieren** (mehrere Ordner)
- **Verschiedene Index-Operationen starten**: Full-Index, EXIF-Update, Rematch
- **Live-Progress beobachten**: Prozentual, mit aktueller Meldung
- **Jobs jederzeit abbrechen**

## Zugriff

1. Starte die Web-App:
   ```bash
   python src/main_web.py
   ```

2. Öffne in deinem Browser:
   ```
   http://127.0.0.1:5000/admin
   ```

## Funktionen

### 1. Foto-Pfade konfigurieren

Trage in der Textarea alle Pfade ein, unter denen Fotos zu finden sind (einer pro Zeile):
```
C:\Meine Fotos
D:\Archiv\2023
E:\Handy
```

### 2. Konfigurationsoptionen

- **Personen-Backend**: 
  - `auto` (Standard): Versucht InsightFace, fällt auf Histogram zurück
  - `insightface`: Schneller und genauer (braucht `requirements-face.txt`)
  - `histogram`: Fallback-Methode, immer verfügbar

- **Worker-Threads**: Bestimmt die Parallelisierung
  - 1 = Single-threaded (langsamer, aber weniger Speicher)
  - 4-8 = Empfohlen für die meisten Systeme

- **Force Re-Index**: Alle Dateien neu verarbeiten (überschreibt Skip-Logic)

- **Nahe Duplikate erkennen**: Markiert ähnliche Bilder als Duplikate (langsamer!)

- **Phash-Schwelle (0-64)**: Nur für Nahe-Duplikate relevant
  - Niedrig = strenger (z.B. 3-5)
  - Hoch = mehr Falsch-Positive (z.B. 10-15)

### 3. Index-Operationen

#### 🔄 Full Indexierung
Scannt alle konfigrierten Pfade und indexiert die Fotos:
- Extrahiert Labels (YOLO-basiert, fallback auf Pfad-Heuristik)
- Extrahiert Personen (mit konfiguriertem Backend)
- Berechnet Duplikate (SHA1 = exakt, pHash = ähnlich)
- Speichert EXIF-Daten

**Skip-Logic**: Dateien mit gleichem Size + Modification-Time werden übersprungen (es sei denn, Force Re-Index ist aktiv).

#### 📊 EXIF aktualisieren
Aktualisiert nur die EXIF-Daten (Aufnahmedatum, GPS, Kamera-Info) für bereits indexierte Fotos. Viel schneller als Full-Index!

#### 👤 Rematch Personen
Berechnet Personen-Matching und Smile-Scores für alle bereits indexierten Fotos neu:
- Nutzt existierende Bilder + bekannte Personen-Embeddings
- Schneller als Full-Index, da Labels/SHA1/pHash erhalten bleiben
- Ideal, wenn du neue Personen hinzugefügt oder den Backend gewechselt hast

### 4. Live-Progress

Während ein Job läuft:
- **Prozentsatz-Balken**: Zeigt Fortschritt visuel
- **Aktuelle/Gesamt-Werte**: z.B. "1250 / 5000"
- **Status-Meldung**: Was gerade verarbeitet wird
- **Laufzeit**: Wie lange der Job bereits läuft
- **Abort-Button**: Brich den Job jederzeit ab

Bei Abbruch:
- Der laufende Thread wird stoppt
- Bisherige Änderungen bleiben in der Datenbank gespeichert
- Status wechselt zu "aborted"

## Technische Details

### Backend-Architektur

```
Routes (/api/admin/...)
    ↓
AdminService (admin_service.py)
    ↓
JobManager (admin_jobs.py) + Thread
    ↓
Index-Logik (ingest.py, persons/service.py, etc.)
```

### Job-Status

Ein Job durchläuft diese States:
- `pending`: Gerade erstellt, noch nicht gestartet
- `running`: Läuft gerade
- `completed`: Erfolgreich beendet
- `failed`: Mit Fehler beendet
- `aborted`: Vom Nutzer abgebrochen

### Progress-Tracking

Alle Operationen nutzen `JobProgress` zum Tracking:
```python
job.current  # Aktuelle Nummer
job.total    # Gesamt
job.percentage  # 0-100
job.message  # Aktuelle Meldung
job.should_abort()  # Check für Abbruch
```

Der Job-Manager speichert alle Jobs in Memory und bereinigt alte Jobs nach 1 Stunde.

## API-Endpoints

Falls du das über externe Tools nutzen möchtest:

```bash
# Full-Index starten
POST /api/admin/config/start-index
{
  "photo_roots": ["C:\\Fotos", "D:\\Archiv"],
  "person_backend": "auto",
  "force_reindex": false,
  "index_workers": 4
}
# → {"job_id": "index_abc123", "status": "started"}

# Job-Status abrufen
GET /api/admin/job/{job_id}
# → {"job_id": "...", "status": "running", "current": 150, "total": 500, ...}

# Job abbrechen
POST /api/admin/job/{job_id}/abort

# Alle Jobs auflisten
GET /api/admin/jobs
```

## Tipps & Tricks

### Performance

- **Einzelner Worker** für kleine Indizes (<1000 Bilder)
- **4-8 Worker** für größere Indizes
- **EXIF-Update ohne Personen**: Schneller wenn nur Datum/GPS brauchst

### Debugging

- Schau in die Browser-Konsole (F12) für JS-Fehler
- Prüfe die Flask-Logs auf Python-Fehler
- Alte Datenbank? `python src/main.py doctor` checkt das

### Speichern der Konfiguration

Momentan wird die Konfiguration nicht automatisch gespeichert. Du kannst sie per JavaScript in `localStorage` speichern (optional-Feature für später).

## Häufige Fragen

**F: Der Job läuft schon lange - ist das normal?**
A: Ja, besonders bei vielen Bildern oder mit InsightFace-Backend. Schau auf den Prozentsatz - wenn er nicht hängt, ist gut.

**F: Kann ich mehrere Jobs gleichzeitig starten?**
A: Ja, die Jobs laufen parallel in separaten Threads. Flask sollte das mit dem Thread-Pool verkraften.

**F: Was passiert, wenn ich die Seite schließe während ein Job läuft?**
A: Der Job läuft weitermachen, bis er fertig oder abgebrochen wird. Wenn die Seite neu geladen wird, kannst du den Status mit der Job-ID wieder abrufen.

**F: Wieso ist "Rematch" schneller als "Full Index"?**
A: Rematch springt YOLO-Label-Erkennung, SHA1/pHash-Berechnung und EXIF-Parsing über - es nutzt die bestehenden Daten aus der DB und berechnet nur Personen-Matches neu.

