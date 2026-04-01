# Admin-Dashboard - Comprehensive Test Suite

## 🧪 Alle Tests ausführen

```bash
# Alle Tests
python -m pytest tests/ -v

# Nur Admin-Tests
python -m pytest tests/test_admin_page.py -v

# Mit Coverage
python -m pytest tests/ --cov=src/app/web --cov-report=html
```

## ✅ Test-Kategorien

### 1. Unit-Tests (Tests)

#### JobManager Tests
```
✓ test_job_manager
  - Job erstellen
  - Progress aktualisieren
  - Status setzen
  - Abort anfordern
  - Job abschließen
  - Job in Dict konvertieren
  - Cleanup alte Jobs
```

#### Flask-App Tests
```
✓ test_flask_app
  - Flask App erstellen
  - Config laden
  - Admin-Routes registriert
  - DB initialisiert
```

### 2. Integration-Tests

#### Job-Lifecycle Tests
```
Job erstellen → Progress setzen → Running → Abort → Completed
```

#### Admin-Service Tests
```
Full-Index Start → Progress Tracking → Completion
EXIF-Update Start → Quick Completion
Rematch Start → Person Matching
```

#### Route Tests
```
GET  /admin           → HTML UI
POST /api/admin/*     → JSON Response
GET  /api/admin/job/* → Job Status
```

## 🏃 Manuelles Testen

### Test 1: Web-UI Accessibility
```bash
# 1. Starte Web-App
python src/main_web.py

# 2. Öffne http://localhost:5000/admin
# 3. Prüfe:
#    ✓ Seite lädt
#    ✓ Alle Formular-Elemente sichtbar
#    ✓ CSS korrekt gerendert
```

### Test 2: Job Creation & Progress
```bash
# 1. Öffne Admin-Dashboard
# 2. Gebe einen Test-Pfad ein (mit ein paar Bildern)
# 3. Klick "Full Indexierung starten"
# 4. Beobachte:
#    ✓ Modal öffnet sich
#    ✓ Progress-Balken aktualisiert sich
#    ✓ Status ändert sich (PENDING → RUNNING)
#    ✓ Prozentsatz erhöht sich
#    ✓ Message wird angezeigt
```

### Test 3: Job Abort
```bash
# 1. Starte einen Full-Index
# 2. Warte bis Status RUNNING ist
# 3. Klick "❌ Abbrechen"
# 4. Prüfe:
#    ✓ Status ändert sich zu "aborted"
#    ✓ Bisherige Daten bleiben in DB
#    ✓ Keine Error-Messages
```

### Test 4: EXIF-Update
```bash
# 1. Starte EXIF-Update
# 2. Beobachte:
#    ✓ Job läuft schnell (1-2ms/Bild)
#    ✓ Status wird zu "completed"
#    ✓ Message zeigt Anzahl Updated
```

### Test 5: Rematch-Personen
```bash
# 1. Starte Rematch mit histogram Backend
# 2. Beobachte:
#    ✓ Job läuft
#    ✓ Status wird zu "completed" oder "aborted"
#    ✓ Keine Fehler in Console (F12)
```

### Test 6: Multiple Jobs
```bash
# 1. Starte Full-Index
# 2. Schließe Modal (aber Job läuft weiter)
# 3. Starte EXIF-Update (gleichzeitig)
# 4. Öffne Admin-Dashboard
# 5. Prüfe:
#    ✓ Beide Jobs sind in der Liste
#    ✓ Beide haben unterschiedliche Job-IDs
#    ✓ Beide haben unterschiedliche Status/Progress
```

### Test 7: Browser Console (F12)
```
Erwartungen:
✓ Keine JavaScript-Fehler
✓ Keine CORS-Fehler
✓ API-Calls erfolgreich (Status 200/201)
```

### Test 8: Flask Logs
```bash
# Im Terminal sehen:
✓ GET /admin 200
✓ POST /api/admin/config/start-index 200
✓ GET /api/admin/job/<id> 200
✓ Keine ERROR-Lines
```

## 📊 Test-Szenarien

### Scenario 1: Happy Path
```
1. Web-App starten
2. Admin öffnen
3. Pfad eingeben
4. Full-Index starten
5. Warten bis completed
6. ✓ Alle Steps erfolgreich
```

### Scenario 2: Abort Flow
```
1. Full-Index starten
2. Nach 5 Sekunden Abort klicken
3. ✓ Status wird zu "aborted"
4. ✓ Keine Fehler
```

### Scenario 3: Multiple Operations
```
1. Full-Index starten
2. Im neuen Tab: Rematch starten
3. Browser-Tab 1: Beobachte Full-Index
4. Browser-Tab 2: Beobachte Rematch
5. ✓ Beide laufen parallel
```

### Scenario 4: Config Validation
```
1. Leere Pfade eingeben
2. Starte Full-Index
3. ✓ Error-Message: "Keine Foto-Pfade angegeben"

1. Ungültigen Pfad eingeben
2. Starte Full-Index
3. ✓ Error-Message: "Pfad existiert nicht: ..."
```

### Scenario 5: Performance under Load
```
1. 1000 Bilder indexieren mit 8 Workers
2. ✓ UI bleibt responsive
3. ✓ Job läuft im Hintergrund
4. ✓ Andere Routes funktionieren
```

## 🔍 Debugging-Checkliste

### Wenn Admin-Page nicht lädt
```
□ Prüfe: python src/main_web.py läuft
□ Prüfe: http://localhost:5000 erreichbar
□ Prüfe: URL ist exakt http://localhost:5000/admin
□ Prüfe: Browser-Console hat keine JS-Fehler
□ Prüfe: Flask-App hat keine Python-Fehler
```

### Wenn Job nicht startet
```
□ Prüfe: Foto-Pfade sind absolut und existieren
□ Prüfe: Pfade haben Schreib-Berechtigungen
□ Prüfe: Flask-Logs zeigen "full_index started"
□ Prüfe: Browser-Console hat keine JS-Fehler
□ Prüfe: /api/admin/job/<id> gibt Status zurück
```

### Wenn Progress nicht aktualisiert wird
```
□ Prüfe: pollJobStatus() wird aufgerufen (DevTools)
□ Prüfe: /api/admin/job/<id> gibt neue Werte zurück
□ Prüfe: UI-Elemente werden mit job.percentage aktualisiert
□ Prüfe: Polling-Intervall ist nicht zu lang (500ms)
```

### Wenn Abort nicht funktioniert
```
□ Prüfe: button.onclick="abortJob()" ist definiert
□ Prüfe: /api/admin/job/<id>/abort antwortet
□ Prüfe: Job.should_abort() wird in Loop geprüft
□ Prüfe: Status ändert sich zu "aborted"
```

## 📝 Test-Report Template

```markdown
# Test Report: Admin-Dashboard

## Test Date
[Datum]

## Environment
- OS: Windows/Linux/Mac
- Python: 3.x
- Browser: Chrome/Firefox/Safari

## Tests Executed

### Unit-Tests
- [ ] JobManager tests passed
- [ ] Flask-App tests passed
- [ ] Route tests passed

### Integration-Tests
- [ ] Job lifecycle working
- [ ] Progress tracking working
- [ ] Abort functionality working
- [ ] Multiple jobs working

### Manual-Tests
- [ ] Web-UI loads correctly
- [ ] Job creation works
- [ ] Job abortion works
- [ ] EXIF-Update works
- [ ] Rematch works
- [ ] Performance acceptable

### Issues Found
[None / List any issues]

### Conclusion
✅ All tests passed / ❌ Some issues found
```

## 🎯 Regressions-Tests

Nach jeder Änderung:

```bash
# Schnell-Test
python -m pytest tests/test_admin_page.py -v

# Voll-Test
python -m pytest tests/ -v

# Coverage prüfen
python -m pytest tests/ --cov=src/app/web
```

Expected:
- ✅ 20/20 tests pass
- ✅ No new warnings
- ✅ Coverage bleibt gleich oder steigt

## 📚 Test-Dokumentation

### Test Files
- `tests/test_admin_page.py` - Admin tests
- `tests/test_web_app.py` - Web integration tests
- `tests/test_person_matching.py` - Person matching tests
- `benchmark_admin.py` - Performance benchmarks

### Test Coverage
```
src/app/web/admin_jobs.py    → 100%
src/app/web/admin_service.py → 85%+
src/app/web/routes.py        → 80%+ (admin endpoints)
```

## ✨ Best-Practices

✓ Jeden Tag einmal vollständigen Test durchführen
✓ Nach Code-Änderungen sofort Regressions-Tests
✓ Benchmark vor/nach Performance-Changes
✓ Fehler-Cases testen (Invalid paths, etc.)
✓ Concurrency testen (Multiple jobs)
✓ UI-Responsiveness prüfen
✓ Browser-Console auf Fehler checken

---

**Test-Status:** ✅ Ready for Production

