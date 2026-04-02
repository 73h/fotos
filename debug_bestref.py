import sys, json
sys.path.insert(0, "src")

from pathlib import Path
from app.persons.service import extract_person_signatures, initialize_person_settings
from app.persons.embeddings import cosine_similarity, initialize_insightface_settings
from app.index.store import get_admin_config
import sqlite3

db_path = Path("data/photo_index.db")
photo_path = Path(r"D:\Fotos\JeHeFotos\Altenberg Sachsen 23.07. - 30.07.2022\20220724_150350.jpg")
person_id = 14  # Heiko

print("=== Schritt 1: Einstellungen laden ===")
initialize_person_settings(db_path)
initialize_insightface_settings(db_path)
admin_config = get_admin_config(db_path)
preferred_backend = str(admin_config.get("person_backend") or "insightface").strip() or None
print(f"  preferred_backend aus config: {repr(preferred_backend)}")

print()
print("=== Schritt 2: Signaturen extrahieren ===")
backend_name, signatures, person_count = extract_person_signatures(photo_path, preferred_backend=preferred_backend)
print(f"  backend_name: {backend_name}")
print(f"  Signaturen: {len(signatures)}")
print(f"  Person-Boxen: {person_count}")

if not signatures:
    print("  FEHLER: Keine Gesichter erkannt!")
    sys.exit(1)

print()
print("=== Schritt 3: Referenzen aus DB laden ===")
with sqlite3.connect(db_path) as conn:
    conn.row_factory = sqlite3.Row
    person_row = conn.execute("SELECT name FROM persons WHERE id = ?", (person_id,)).fetchone()
    print(f"  Person gefunden: {person_row['name'] if person_row else 'NICHT GEFUNDEN'}")

    refs = conn.execute(
        "SELECT source_path, vector_json, backend FROM person_refs WHERE person_id = ?",
        (person_id,)
    ).fetchall()
    print(f"  Gesamt-Referenzen für Person {person_id}: {len(refs)}")

    backends_in_refs = set(r["backend"] for r in refs)
    print(f"  Backends in Referenzen: {backends_in_refs}")

    refs_filtered = conn.execute(
        "SELECT source_path, vector_json FROM person_refs WHERE person_id = ? AND lower(backend) = lower(?)",
        (person_id, backend_name),
    ).fetchall()
    print(f"  Referenzen für backend '{backend_name}': {len(refs_filtered)}")

if not refs_filtered:
    print()
    print(f"  PROBLEM: Keine Referenzen für backend '{backend_name}' gefunden!")
    print(f"  Aber Referenzen vorhanden für: {backends_in_refs}")
    sys.exit(1)

print()
print("=== Schritt 4: Scores berechnen ===")
best_score = -1.0
best_source_path = None
for ref in refs_filtered:
    vector = [float(v) for v in json.loads(ref["vector_json"])]
    for sig, _ in signatures:
        score = cosine_similarity(sig, vector)
        if score > best_score:
            best_score = score
            best_source_path = ref["source_path"]

print(f"  Bester Score: {best_score:.4f}")
print(f"  Beste Quelle: {best_source_path}")
print()
print("=== ERGEBNIS: OK ===")

