## Start WebUI (mit GPU-Acceleration)

``` Shell
.\.venv\Scripts\Activate.ps1
. ./setup_gpu.ps1               # GPU-Einstellungen laden
python src/main.py web --db "data/photo_index.db" --cache-dir "data/cache" --host 0.0.0.0 --port 5050
```

## Force Reindex (mit GPU-Acceleration)

``` Shell
.\.venv\Scripts\Activate.ps1
. ./setup_gpu.ps1               # GPU-Einstellungen laden
python src/main.py index --root "D:\Fotos" --root "C:\Users\heiko\Pictures\iCloud Photos" --person-backend insightface --index-workers 10 --force-reindex   
```

## GPU-Setup ueberpruefen

``` Shell
.\.venv\Scripts\Activate.ps1
. ./setup_gpu.ps1
python src/main.py doctor
```

Siehe auch: `GPU_SETUP.md` fuer ausführliche Dokumentation
