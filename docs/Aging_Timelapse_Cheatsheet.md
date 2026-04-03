# Aging-Timelapse Cheatsheet (10-15s)

Ich erstelle dir jetzt ein kompaktes **Ziellaengen-Cheatsheet nach Fotoanzahl** und gebe dir dazu eine einfache Formel, damit du schnell passende `Hold`/`Morph`-Werte findest.

- [x] Ziel auf 10-15 Sekunden festlegen
- [x] Preset-Tabelle fuer typische Fotoanzahlen (20/40/80/120) geben
- [x] Schnellformel fuer eigene Alben ergaenzen
- [x] Kurzempfehlung fuer realistischen Look (inkl. AI-Backend)

Fuer die Dauer gilt in eurem Timelapse:
`Dauer = (N * Hold + (N - 1) * Morph) / FPS`
(`N` = verwendete Gesichts-Fotos)

## Cheatsheet (Ziel: kurz + fluessig + realistisch)

| Fotos `N` | Ziel ~10s | Ziel ~12s | Ziel ~15s |
|---|---:|---:|---:|
| 20 | `Hold 3 / Morph 10` | `Hold 4 / Morph 11` | `Hold 5 / Morph 14` |
| 40 | `Hold 2 / Morph 4` | `Hold 3 / Morph 5` | `Hold 3 / Morph 7` |
| 80 | `Hold 1 / Morph 2` | `Hold 1 / Morph 3` | `Hold 2 / Morph 3` |
| 120 | `Hold 1 / Morph 1` | `Hold 1 / Morph 2` | `Hold 1 / Morph 2` |

Annahmen: `FPS = 24`, `Interpolator = flow`.

## Empfohlene Basis-Qualitaet dazu

- `FPS`: `24`
- `Size`: `1024` (oder `768`, wenn's zu langsam wird)
- `Quality`: `balanced`
- `Interpolator`: `flow`
- `Smooth`: `0.12` bis `0.16`
- `Detail`: `0.08` bis `0.15`
- `Face-Enhancement`: `an`
- `AI-Mode`: `off` (fuer maximal natuerlichen Look)

## Wenn du AI trotzdem willst (dezent)

- `Quality`: `max` (nur dann wird AI ueberhaupt angewandt)
- `AI-Mode`: `max`
- `AI-Backend`: `onnx` (wenn verfuegbar), sonst `auto`
- `AI-Strength`: `0.20` bis `0.35`
- `Detail` niedrig halten (`<= 0.15`), sonst schnell "ueberschaerft".

