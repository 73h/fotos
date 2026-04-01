"""Quick Test um die Progressbar zu demonstrieren."""

import tempfile
from pathlib import Path

from PIL import Image

# Erstelle temp Bilderordner
tmp_dir = Path(tempfile.mkdtemp())
for i in range(5):
    img = Image.new("RGB", (100, 100), color=(i * 50, i * 30, i * 20))
    img.save(tmp_dir / f"test_{i:02d}.jpg")

print(f"\nTest-Bilder erstellt in: {tmp_dir}")
print(f"Starten Sie jetzt:\n")
print(f"python src/main.py index --root {tmp_dir}")
print(f"\nSie sehen eine Progressbar wie:\n")
print("Indexiere Fotos: 60%|████████░░░░| 3/5 [00:02<00:01, 1.50 Foto/s]\n")

