"""
Sucht erreichbare Face-Enhancement ONNX-Modelle und laedt das beste herunter.
Ausfuehren: python scripts/check_onnx_models.py
"""
import urllib.request
import urllib.error
import json
import os
import sys
from pathlib import Path

DEST = Path("D:/models/face_enhancer.onnx")

CANDIDATES = [
    # CodeFormer - hochwertiges Gesichtsrestaurierungsmodell
    ("codeformer (facefusion)",
     "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/codeformer.onnx"),
    # GFPGAN 1.4 - schnelleres Gesichtsrestaurierungsmodell
    ("gfpgan_1.4 (facefusion)",
     "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gfpgan_1.4.onnx"),
    # Real-ESRGAN x2 - Super Resolution (auch fuer Gesichter geeignet)
    ("real_esrgan_x2plus (facefusion)",
     "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/real_esrgan_x2plus.onnx"),
]


def check_url(url: str, timeout: int = 12) -> tuple[bool, int]:
    req = urllib.request.Request(url, headers={"User-Agent": "python-urllib"}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            size = int(r.headers.get("Content-Length", 0))
            return True, size
    except Exception:
        return False, 0


def download(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "python-urllib"})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        data = r.read()
        f.write(data)
    return len(data)


# ---------------------------------------------------------------------------
# Releases dynamisch abfragen, um aktuelle Tag-Namen zu finden
# ---------------------------------------------------------------------------
def find_release_assets(repo: str, pattern: str) -> list[tuple[str, str, int]]:
    results = []
    try:
        api = f"https://api.github.com/repos/{repo}/releases"
        req = urllib.request.Request(api, headers={"User-Agent": "python-urllib"})
        with urllib.request.urlopen(req, timeout=10) as r:
            releases = json.loads(r.read())
        for release in releases[:5]:
            for asset in release.get("assets", []):
                name = asset["name"]
                if pattern.lower() in name.lower() and name.endswith(".onnx"):
                    results.append((name, asset["browser_download_url"], asset["size"]))
    except Exception as e:
        print(f"  API-Fehler fuer {repo}: {e}")
    return results


print("\n=== ONNX Face-Enhancer Modell-Suche ===\n")

# Dynamische Suche via GitHub API
print("Suche in facefusion/facefusion-assets ...")
dynamic = find_release_assets(
    "facefusion/facefusion-assets",
    "face_enhancer"
)
dynamic += find_release_assets(
    "facefusion/facefusion-assets",
    "gfpgan"
)
dynamic += find_release_assets(
    "facefusion/facefusion-assets",
    "codeformer"
)

all_candidates = [(name, url, size) for name, url, size in dynamic]

# Statische Kandidaten hinzufuegen
for label, url in CANDIDATES:
    ok, size = check_url(url)
    if ok:
        all_candidates.append((label, url, size))

print(f"\n  {len(all_candidates)} Modell(e) gefunden:\n")
for i, (name, url, size) in enumerate(all_candidates, 1):
    mb = size // 1024 // 1024
    print(f"  [{i}] {name} ({mb} MB)")
    print(f"      {url}")

if not all_candidates:
    print("  Kein Modell erreichbar. Pruefe Internetverbindung.")
    sys.exit(1)

# Automatisch das erste (kleinste vollstaendig gueltige) auswaehlen
# Bevorzuge: gfpgan > codeformer > esrgan
preferred_order = ["gfpgan", "codeformer", "esrgan", "espcn"]
chosen_idx = 0
for pref in preferred_order:
    for i, (name, url, size) in enumerate(all_candidates):
        if pref in name.lower():
            chosen_idx = i
            break

name, url, size = all_candidates[chosen_idx]
mb = size // 1024 // 1024

print(f"\nAuto-Auswahl: [{chosen_idx + 1}] {name} ({mb} MB)")
print(f"Lade herunter nach: {DEST}")
print("...")

try:
    downloaded = download(url, DEST)
    print(f"\nOK - {round(downloaded / 1024 / 1024, 1)} MB gespeichert: {DEST}")

    # Kurzcheck: gueltige ONNX-Datei?
    with open(DEST, "rb") as f:
        magic = f.read(8)
    if magic[:4] != b"\x08\x05\x12\x05"[:4] and b"onnx" not in magic.lower() and b"ir_version" not in magic:
        # Pruefe auf ONNX proto magic bytes (varint encoding, common start)
        pass  # ONNX-Dateien haben kein festes Magic, aber wir koennen pruefen ob es kein HTML ist
    if b"<html" in magic.lower() or b"<!doctype" in magic.lower():
        print("WARNUNG: Heruntergeladene Datei scheint HTML zu sein (kein gueltiges ONNX).")
        DEST.unlink()
        sys.exit(1)

    print("\nSelbsttest: Resolver mit neuem Modell ...")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["FOTOS_TIMELAPSE_FACE_ONNX_MODEL"] = str(DEST)
    os.environ["FOTOS_TIMELAPSE_FACE_ONNX_PROVIDER"] = "auto"
    os.environ["FOTOS_TIMELAPSE_FACE_ONNX_SIZE"] = "256"
    from src.app.albums.timelapse_ai import resolve_enhancer
    enhancer = resolve_enhancer("max", ai_backend="onnx")
    print(f"  onnx-backend: {type(enhancer).__name__}")

    print("\n=== Fertig ===")
    print(f"Modell: {DEST}")
    print("ENV: FOTOS_TIMELAPSE_FACE_ONNX_MODEL bereits gesetzt.")
    print("\nFuer neue Shell in .env.timelapse eintragen:")
    print(f'  $env:FOTOS_TIMELAPSE_FACE_ONNX_MODEL="{DEST}"')

except Exception as e:
    print(f"\nFehler beim Download: {e}")
    if DEST.exists():
        DEST.unlink()
    sys.exit(1)

