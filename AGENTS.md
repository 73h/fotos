# AGENTS.md

## Projektbild in 60 Sekunden
- `fotos` ist lokal-first: CLI + Flask-Web-UI laufen auf einer gemeinsamen SQLite-DB (`data/photo_index.db`), keine Cloud-Pipeline (`src/app/cli.py`, `src/app/web/__init__.py`, `src/app/index/store.py`).
- Der zentrale Datenfluss ist `scan_images` -> Label/Personen-Erkennung -> `upsert_photo` + Match-Persistenz (`src/app/ingest.py`, `src/app/detectors/labels.py`, `src/app/persons/service.py`, `src/app/index/store.py`).
- Suche basiert auf `photos.search_blob` plus SQL-Filter (`person:`, `smile:`, `month:`, `year:`) aus `parse_search_filters` (`src/app/index/store.py`).
- Album- und Timelapse-Funktionen nutzen dieselbe DB; Timelapse-Jobs laufen in-process mit einfachem Job-Status-Dict (`src/app/web/routes.py`, `src/app/albums/timelapse.py`).

## Wichtige Komponenten und Grenzen
- **CLI-Orchestrierung:** Alle User-Workflows starten in `_build_parser`/`main` und delegieren an Modul-Funktionen (`src/app/cli.py`).
- **Index-Store als Kernschicht:** Schema, Migrationen, Such-SQL, Duplikate und EXIF-Updates liegen gebuendelt in `src/app/index/store.py`.
- **Personenmatching:** `persons/service.py` orchestriert Backend-Auswahl, Signatur-Extraktion, Top-K-Matching und Persistenz via `persons/store.py`.
- **Web-Layer:** `run_search_page` + Album-/Person-Lookups in `routes.py`; keine separate Service-Schicht zwischen Flask-Route und Store.

## Entwickler-Workflows (bewaehrte Reihenfolge)
- Setup/Smoke-Test laut `README.md`: `python src/main.py doctor`, dann `index`, dann `web`.
- Standardtests: `python -m pytest -q`.
- Inkrementelles Index-Verhalten ist kritisch; Regressionen zuerst mit `tests/test_index_incremental.py` absichern.
- Web/API-Regressionen (Pagination, Album, Filter, Timelapse-Endpunkte) in `tests/test_web_app.py` pruefen.
- Schnellere Neubewertung von Personen/Smile ohne Vollindex: `python src/main.py rematch-persons --workers N`.

## Projekt-spezifische Muster, die Agents beachten sollen
- Immer `ensure_schema(db_path)` vor DB-Zugriffen aufrufen; das ist zugleich Migrationspfad fuer alte DBs.
- Persistenz ist "replace statt merge" fuer Personen-Matches/Referenzen (`replace_person_references`, `replace_photo_person_matches`).
- Query-Filterlogik ist doppelt vorhanden (Store + Web-Map-Filter). Bei neuen Filtern beide Pfade konsistent erweitern (`parse_search_filters`, `_build_photo_filter_clause`).
- Der Index-Skip basiert auf `(size_bytes, modified_ts, exif_checked)`; Aenderungen daran beeinflussen Performance stark (`_index_command`, `get_photo_metadata_map`).
- Personenerkennung ist backend-abhängig (`auto|insightface|histogram`) und kann auf Histogramm fallen; keine InsightFace-Pflicht annehmen (`src/app/persons/embeddings.py`).
- Tokens fuer Web-Fotozugriff sind URL-safe Base64 von Pfaden; Route-Integrationen muessen `_encode_path`/`_decode_path` wiederverwenden.

## Externe Integrationen und Risiken
- YOLO (`ultralytics`) ist optional zur Klassifikation; bei Fehlern greift Pfad-Keyword-Heuristik (`src/app/detectors/labels.py`).
- InsightFace/ONNX ist optional (`requirements-face.txt`), Smile-Score kommt nur ueber passende Backend-Faces zustande.
- Karten-Geocoding nutzt Nominatim via `urllib`; Antworten werden per `@lru_cache` im Prozess gepuffert (`src/app/web/routes.py`).
- Timelapse benoetigt `opencv-python`, optional `scipy` fuer Delaunay-Morphing; ohne `scipy` Cross-Fade-Fallback (`src/app/albums/timelapse.py`).

## Wenn du als Agent Code aenderst
- Neue CLI-Features immer in Parser + Command-Funktion + ggf. README-Commandbeispiele nachziehen (`src/app/cli.py`, `README.md`).
- Bei Schema-/Filter-/Matching-Aenderungen mindestens die zugehoerigen Tests in `tests/test_index_incremental.py`, `tests/test_person_matching.py`, `tests/test_web_app.py` anpassen/erweitern.
- Pfad- und Cache-Aufloesung ueber `AppConfig.resolve_db_path/resolve_cache_dir` statt Hardcoding (`src/app/config.py`).

