import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.albums.export import _draw_metadata_overlay, _fit_overlay_font, _text_size  # noqa: E402


class AlbumExportOverlayTests(unittest.TestCase):
    def test_fit_overlay_font_aims_for_target_text_height(self) -> None:
        image = Image.new("RGB", (1200, 800), color=(120, 120, 120))
        draw = ImageDraw.Draw(image)
        target_height = int(round(image.height * 0.05))

        text = "Muenchen, 03.04.2026"
        font = _fit_overlay_font(draw, text, target_height)
        _, measured_height = _text_size(draw, text, font)

        self.assertGreaterEqual(measured_height, target_height - 2)

    def test_draw_metadata_overlay_places_text_bottom_right_with_expected_height(self) -> None:
        base = Image.new("RGB", (1200, 800), color=(200, 200, 200))
        original = base.copy()

        result = _draw_metadata_overlay(base, "03.04.2026", "Muenchen")
        diff = ImageChops.difference(original, result).convert("L")
        bbox = diff.getbbox()

        self.assertIsNotNone(bbox)
        assert bbox is not None

        changed_width = bbox[2] - bbox[0]
        changed_height = bbox[3] - bbox[1]

        self.assertGreater(bbox[0], int(result.width * 0.45))
        self.assertGreater(bbox[1], int(result.height * 0.75))
        self.assertGreaterEqual(changed_height, int(round(result.height * 0.05)) - 2)
        self.assertGreater(changed_width, int(result.width * 0.1))

    def test_visual_comparison_exact_mode_is_closer_to_target_height(self) -> None:
        target_image = Image.new("RGB", (1200, 800), color=(180, 180, 180))
        overlay_text_date = "03.04.2026"
        overlay_text_place = "Muenchen"
        legacy = _draw_metadata_overlay(
            target_image.copy(),
            overlay_text_date,
            overlay_text_place,
            exact_text_height=False,
        )
        exact = _draw_metadata_overlay(
            target_image.copy(),
            overlay_text_date,
            overlay_text_place,
            exact_text_height=True,
        )

        legacy_bbox = ImageChops.difference(target_image, legacy).convert("L").getbbox()
        exact_bbox = ImageChops.difference(target_image, exact).convert("L").getbbox()

        self.assertIsNotNone(legacy_bbox)
        self.assertIsNotNone(exact_bbox)
        assert legacy_bbox is not None
        assert exact_bbox is not None

        legacy_height = legacy_bbox[3] - legacy_bbox[1]
        exact_height = exact_bbox[3] - exact_bbox[1]
        mode_diff_bbox = ImageChops.difference(legacy, exact).convert("L").getbbox()

        self.assertIsNotNone(mode_diff_bbox)
        self.assertGreaterEqual(exact_height, legacy_height)


if __name__ == "__main__":
    unittest.main()

