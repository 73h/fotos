import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.app.cli as cli_module  # noqa: E402
import src.app.detectors.labels as labels_module  # noqa: E402
from src.app.config import AppConfig  # noqa: E402


class _FakeTensor:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)


class _FakeBoxes:
    def __init__(self, cls_values, conf_values, xyxy_values):
        self.cls = _FakeTensor(cls_values)
        self.conf = _FakeTensor(conf_values)
        self.xyxy = _FakeTensor(xyxy_values)


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeModel:
    def __init__(self):
        self.names = {
            0: "person",
            15: "cat",
            56: "chair",
        }
        self.calls = []

    def predict(self, source: str, conf: float, verbose: bool, device: str):
        self.calls.append(
            {
                "source": source,
                "conf": conf,
                "verbose": verbose,
                "device": device,
            }
        )
        return [
            _FakeResult(
                _FakeBoxes(
                    cls_values=[15, 56, 0],
                    conf_values=[0.91, 0.83, 0.77],
                    xyxy_values=[
                        [10, 20, 110, 210],
                        [120, 60, 280, 240],
                        [5, 5, 50, 100],
                    ],
                )
            )
        ]


class DetectObjectsTests(unittest.TestCase):
    def test_detect_objects_and_summary_are_differentiated(self) -> None:
        fake_model = _FakeModel()
        original_load_model = labels_module._load_model
        original_model_name = labels_module._YOLO_MODEL_NAME
        original_confidence = labels_module._YOLO_CONFIDENCE
        original_device = labels_module._YOLO_DEVICE

        labels_module._load_model = lambda: fake_model
        labels_module._YOLO_MODEL_NAME = "fake-yolo.pt"
        labels_module._YOLO_CONFIDENCE = 0.42
        labels_module._YOLO_DEVICE = "cpu"

        try:
            detections = labels_module.detect_objects(
                Path("demo.jpg"),
                include_person=False,
                label_filter={"cat", "chair", "person"},
            )
            self.assertEqual([detection.label for detection in detections], ["cat", "chair"])
            self.assertEqual(detections[0].kind, "animal")
            self.assertEqual(detections[0].group, "pet")
            self.assertEqual(detections[1].kind, "object")
            self.assertEqual(detections[1].group, "furniture")
            self.assertEqual(detections[0].bbox, (10, 20, 110, 210))

            summary = labels_module.summarize_object_detections(
                Path("demo.jpg"),
                include_person=False,
                label_filter={"cat", "chair"},
            )
            self.assertEqual(summary.model_name, "fake-yolo.pt")
            self.assertEqual(summary.labels, ["cat", "chair"])
            self.assertEqual(summary.counts_by_label, {"cat": 1, "chair": 1})
            self.assertEqual(summary.counts_by_kind, {"animal": 1, "object": 1})
            self.assertEqual(summary.counts_by_group, {"furniture": 1, "pet": 1})
            self.assertEqual(fake_model.calls[0]["device"], "cpu")
            self.assertAlmostEqual(fake_model.calls[0]["conf"], 0.42, places=6)
        finally:
            labels_module._load_model = original_load_model
            labels_module._YOLO_MODEL_NAME = original_model_name
            labels_module._YOLO_CONFIDENCE = original_confidence
            labels_module._YOLO_DEVICE = original_device

    def test_infer_labels_from_path_keeps_index_compatibility(self) -> None:
        original_detect_objects = labels_module.detect_objects

        labels_module.detect_objects = lambda *args, **kwargs: [
            labels_module.ObjectDetection(
                label="cat",
                kind="animal",
                group="pet",
                confidence=0.9,
                bbox=(0, 0, 10, 10),
            ),
            labels_module.ObjectDetection(
                label="chair",
                kind="object",
                group="furniture",
                confidence=0.8,
                bbox=(10, 10, 20, 20),
            ),
        ]

        try:
            labels = labels_module.infer_labels_from_path(Path("gallery/demo.jpg"))
            self.assertEqual(labels, ["animal", "object"])
        finally:
            labels_module.detect_objects = original_detect_objects

    def test_detect_objects_command_writes_json_report_for_directory(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            workspace = Path(tmp_dir)
            photos_dir = workspace / "photos"
            photos_dir.mkdir(parents=True, exist_ok=True)
            report_path = workspace / "reports" / "detections.json"

            for file_name in ("cat.jpg", "chair.jpg"):
                image = Image.new("RGB", (64, 64), color=(120, 80, 180))
                image.save(photos_dir / file_name)

            config = AppConfig.from_workspace(workspace_root=workspace)

            original_get_classes = cli_module.get_supported_yolo_classes
            original_configure = cli_module.configure_yolo_runtime
            original_summarize = cli_module.summarize_object_detections
            seen_filters = []

            def fake_summarize(path: Path, include_person: bool, label_filter):
                seen_filters.append(set(label_filter or set()))
                if path.stem == "cat":
                    detections = [
                        labels_module.ObjectDetection(
                            label="cat",
                            kind="animal",
                            group="pet",
                            confidence=0.95,
                            bbox=(1, 2, 30, 40),
                        )
                    ]
                else:
                    detections = [
                        labels_module.ObjectDetection(
                            label="chair",
                            kind="object",
                            group="furniture",
                            confidence=0.88,
                            bbox=(5, 8, 40, 55),
                        )
                    ]
                return labels_module.ObjectDetectionSummary(
                    path=str(path),
                    model_name="fake-yolo.pt",
                    confidence_threshold=0.3,
                    device="cpu",
                    labels=[detection.label for detection in detections],
                    counts_by_label={detections[0].label: 1},
                    counts_by_kind={detections[0].kind: 1},
                    counts_by_group={detections[0].group: 1},
                    detections=detections,
                )

            cli_module.get_supported_yolo_classes = lambda: ["cat", "chair", "person"]
            cli_module.configure_yolo_runtime = lambda **kwargs: None
            cli_module.summarize_object_detections = fake_summarize

            try:
                rc = cli_module._detect_objects_command(
                    config=config,
                    raw_inputs=[str(photos_dir)],
                    custom_db_path=None,
                    model_name="fake-yolo.pt",
                    confidence=0.3,
                    device="cpu",
                    raw_labels="cat,chair",
                    include_person=False,
                    json_output=True,
                    output_path=str(report_path),
                )
                self.assertEqual(rc, 0)
                self.assertTrue(report_path.exists())

                payload = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(len(payload), 2)
                labels_by_name = {
                    Path(entry["path"]).stem: entry["labels"]
                    for entry in payload
                }
                self.assertEqual(labels_by_name["cat"], ["cat"])
                self.assertEqual(labels_by_name["chair"], ["chair"])
                self.assertEqual(seen_filters, [{"cat", "chair"}, {"cat", "chair"}])
            finally:
                cli_module.get_supported_yolo_classes = original_get_classes
                cli_module.configure_yolo_runtime = original_configure
                cli_module.summarize_object_detections = original_summarize


if __name__ == "__main__":
    unittest.main()

