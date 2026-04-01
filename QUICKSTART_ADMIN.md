# Admin-Dashboard Quick-Start Guide

## 🚀 5-Minuten Setup

### 1. Web-App starten
```bash
python src/main_web.py
```
Server läuft auf `http://localhost:5000`

### 2. Admin-Dashboard öffnen
Öffne im Browser:
```
http://localhost:5000/admin
```

### 3. Foto-Pfade konfigurieren
Gebe deine Foto-Verzeichnisse ein (eine pro Zeile):
```
C:\Users\Marie\Pictures
D:\Archive\2023
E:\iPhone_Backup
```

### 4. Standard-Optionen wählen
- **Backend:** `auto` (Standard)
- **Worker:** `4` (für die meisten Systeme optimal)
- **Force Re-Index:** ❌ (nur falls nötig)
- **Nahe Duplikate:** ❌ (macht es langsamer)

### 5. Full-Index starten
Klick auf "Starten" → Modal öffnet sich → Progress wird angezeigt

---

## 📋 Use-Cases

### Use-Case 1: Erste Indexierung (großer Fotobestand)
```
Foto-Pfade: C:\Fotos (5000+ Bilder)
Backend:    auto
Worker:     8 (max parallel)
Force:      ❌
Duration:   ~30-60 Minuten
```

### Use-Case 2: Wöchentliche inkrementelle Updates
```
Foto-Pfade: C:\Fotos (neu hinzugefügte Bilder)
Backend:    auto
Worker:     4
Force:      ❌ (Skip-Logic nutzen)
Duration:   ~5-10 Minuten
```

### Use-Case 3: EXIF-Daten aktualisieren (nach Geo-Tagging)
```
Operation:  EXIF aktualisieren
Duration:   ~1-2 Minuten (sehr schnell!)
```

### Use-Case 4: Backend-Wechsel (z.B. auf InsightFace)
```
Operation:  Rematch Personen
Backend:    insightface
Worker:     4
Duration:   ~10-20 Minuten
```

---

## ⚙️ Empfohlene Worker-Counts

| Hardware | CPUs | Empfohlen | Max |
|----------|------|-----------|-----|
| Laptop (2-4 Kerne) | 2-4 | 2 | 4 |
| Standard PC (4-8 Kerne) | 4-8 | 4 | 8 |
| Gaming PC (8-16 Kerne) | 8-16 | 8 | 16 |
| Server (16+ Kerne) | 16+ | 16 | 32 |

**Faustregel:** Worker = Anzahl CPUs / 2 (um System nicht zu überlasten)

---

## 💡 Tipps & Tricks

### Tipp 1: Progress speichern
Wenn der Job läuft und dein Computer neustartet:
- Der Job wird unterbrochen
- Bisherige Daten bleiben in der DB
- Einfach neu starten → Skip-Logic nutzt sich noch nicht verarbeitete Dateien

### Tipp 2: Mehrere Pfade gleichzeitig
```
C:\Fotos\2020
C:\Fotos\2021
C:\Fotos\2022
D:\Handy_Backup
E:\Archive
```
Alle werden in einem Job verarbeitet ✓

### Tipp 3: EXIF-Daten ohne Personen
Falls du nur Datum/GPS brauchst:
→ EXIF-Update starten (viel schneller)

### Tipp 4: Backend-Fehler?
Falls InsightFace Fehler wirft:
→ Backend auf `histogram` setzen (Fallback, immer verfügbar)

---

## 🔍 Debugging

### Problem: Job läuft sehr lange
**Lösung:**
- Schau auf den Prozentsatz (läuft noch? oder hängt?)
- Bei InsightFace-Backend: Normal 10-50ms/Bild
- Bei Histogram-Backend: Schneller 2-5ms/Bild

### Problem: Fehler im Modal angezeigt
**Lösung:**
- Prüfe Browser-Konsole (F12 → Console)
- Lese die Fehler-Meldung
- Prüfe Flask-Logs im Terminal

### Problem: Admin-Seite zeigt "404"
**Lösung:**
```bash
# Stelle sicher dass die App richtig läuft
python src/main_web.py
# Prüfe dass http://localhost:5000 erreichbar ist
```

---

## 📊 Performance erwarten

### Full-Index Performance
```
Small Index (100-500 Bilder):     1-5 Minuten
Medium Index (500-2000 Bilder):   5-20 Minuten
Large Index (2000-10000 Bilder):  20-60 Minuten
Huge Index (10000+ Bilder):       1-3 Stunden
```

### Mit Worker-Parallelisierung
```
1 Worker:  Baseline
4 Workers: 3-3.5x schneller
8 Workers: 5-6x schneller (bei genug Speicher)
```

---

## ✅ Schritt-für-Schritt Anleitung

### Schritt 1: Web-App starten
```powershell
cd D:\Code\fotos
python src/main_web.py
```
Output:
```
 * Running on http://127.0.0.1:5000
 * Press CTRL+C to quit
```

### Schritt 2: Admin-Seite öffnen
Browser: `http://localhost:5000/admin`

### Schritt 3: Foto-Pfade eingeben
```
C:\Users\Marie\Pictures
D:\Archive\2023
```

### Schritt 4: Optionen anpassen (optional)
- Worker: 4
- Rest: Default-Werte OK

### Schritt 5: "Starten" klicken
Modal öffnet sich mit Live-Progress

### Schritt 6: Warten & zuschauen
Prozentsatz-Balken zeigt Fortschritt:
```
Progress: 150 / 5000 (3%)
Message: Verarbeitet: 150, Duplikate: 2
```

### Schritt 7: Job fertig?
Warte bis Status zu "completed" wechselt

### Schritt 8: Fotos durchsuchen
Gehe zu `http://localhost:5000/` und nutze Search

---

## 🎯 Nächste Schritte nach der Indexierung

1. **Fotos durchsuchen:** http://localhost:5000
2. **Personen anlegen:** Über UI Personen-Referenzen erstellen
3. **Alben erstellen:** Favorite Fotos in Alben organisieren
4. **Timelapses erstellen:** Videos aus Foto-Serien erzeugen

---

## 🆘 Häufige Fragen

**F: Kann ich mehrere Jobs gleichzeitig starten?**
A: Ja! Jeder Job läuft in einem eigenen Thread.

**F: Was ist der Unterschied zwischen Full-Index und Rematch?**
A: Full-Index verarbeitet alles (Labels, Personen, Duplikate). Rematch nur Personen-Matching.

**F: Wie kann ich einen Job abbrechen?**
A: Klick auf "❌ Abbrechen" im Progress-Modal während der Job läuft.

**F: Gehen Daten verloren bei Abbruch?**
A: Nein! Bereits verarbeitete Bilder werden gespeichert. Nur nicht-gestartete Bilder werden übersprungen beim nächsten Lauf (Skip-Logic).

**F: Welches Backend sollte ich verwenden?**
A: 
- `auto`: Beste Option, nutzt InsightFace wenn verfügbar
- `insightface`: Schneller und genauer (braucht GPU)
- `histogram`: Fallback, langsamer aber reliable

---

## 📝 Beispiel-Konfiguration speichern

Falls du eine Konfiguration oft nutzt, kannst du diese merken:

**Konfiguration 1: Schneller Rematch**
```
Backend: auto
Worker: 8
Operation: Rematch
```

**Konfiguration 2: Volle Indexierung (neu)**
```
Backend: auto
Worker: 4
Force: ❌
Duplicates: ❌
```

**Konfiguration 3: Nur EXIF**
```
Operation: EXIF aktualisieren
(schneller für GPS-Tagging)
```

---

## ✨ Best-Practices

✓ Starte Full-Index nachts (lange Laufzeit)
✓ Nutze EXIF-Update nach Geo-Tagging
✓ Rematch nach Backend-Wechsel
✓ Worker-Count = CPU-Kerne / 2
✓ Checke Skip-Logic Status in der Meldung
✓ Mehrere Pfade = ein Job (effizienter)

---

**Viel Erfolg bei der Indexierung! 🎉**

