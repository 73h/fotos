import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.albums.timelapse_ai import (  # noqa: E402
    CompositeEnhancer,
    LocalAIMaxEnhancer,
    NoopEnhancer,
    OnnxFaceEnhancer,
    enhance_sequence_with_ai,
    resolve_enhancer,
)


class TimelapseAIMvpTests(unittest.TestCase):
    def test_resolve_enhancer_off_returns_noop(self) -> None:
        enhancer = resolve_enhancer("off")
        self.assertIsInstance(enhancer, NoopEnhancer)

    def test_enhance_sequence_with_ai_off_keeps_frame_count(self) -> None:
        frames = [
            np.zeros((32, 32, 3), dtype=np.uint8),
            np.ones((32, 32, 3), dtype=np.uint8) * 30,
        ]
        out = enhance_sequence_with_ai(frames, ai_mode="off", ai_backend="auto", ai_strength=0.8)
        self.assertEqual(len(out), 2)
        self.assertTrue(np.array_equal(out[0], frames[0]))
        self.assertTrue(np.array_equal(out[1], frames[1]))

    def test_enhance_sequence_with_ai_falls_back_on_enhancer_error(self) -> None:
        class _BrokenEnhancer:
            def enhance_sequence(self, frames, strength, progress_cb=None):
                raise RuntimeError("boom")

        frames = [np.ones((24, 24, 3), dtype=np.uint8) * 99]
        with patch("src.app.albums.timelapse_ai.resolve_enhancer", return_value=_BrokenEnhancer()):
            out = enhance_sequence_with_ai(frames, ai_mode="auto", ai_backend="auto", ai_strength=0.5)

        self.assertEqual(len(out), 1)
        self.assertTrue(np.array_equal(out[0], frames[0]))

    def test_resolve_enhancer_auto_defaults_to_local(self) -> None:
        enhancer = resolve_enhancer("auto", ai_backend="auto", config={})
        self.assertIsInstance(enhancer, LocalAIMaxEnhancer)

    def test_resolve_enhancer_superres_without_valid_model_falls_back_to_local(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            invalid_path = str(Path(tmp_dir) / "missing.onnx")
            enhancer = resolve_enhancer(
                "max",
                ai_backend="superres",
                config={
                    "timelapse_ai_backend": "superres",
                    "timelapse_superres_model": invalid_path,
                },
            )
        self.assertIsInstance(enhancer, LocalAIMaxEnhancer)

    def test_resolve_enhancer_auto_with_model_uses_composite(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            model_path = Path(tmp_dir) / "dummy.onnx"
            model_path.write_bytes(b"not-a-real-model")
            enhancer = resolve_enhancer(
                "auto",
                ai_backend="auto",
                config={
                    "timelapse_ai_backend": "auto",
                    "timelapse_superres_model": str(model_path),
                },
            )
        self.assertIsInstance(enhancer, CompositeEnhancer)

    def test_resolve_enhancer_onnx_with_model_and_runtime_uses_onnx_backend(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            model_path = Path(tmp_dir) / "face_enhance.onnx"
            model_path.write_bytes(b"fake-onnx")
            with patch("src.app.albums.timelapse_ai._can_use_onnxruntime", return_value=True):
                enhancer = resolve_enhancer(
                    "max",
                    ai_backend="onnx",
                    config={
                        "timelapse_face_onnx_model": str(model_path),
                    },
                )
        self.assertIsInstance(enhancer, OnnxFaceEnhancer)


if __name__ == "__main__":
    unittest.main()

