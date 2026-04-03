"""Diagnose-Tools fuer das Foto-MVP."""

import sys
from pathlib import Path

import torch

from .detectors import labels as yolo_labels
from .persons.embeddings import resolve_backend
from .index.store import get_admin_config


def _check_python() -> dict:
    return {
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "executable": sys.executable,
    }


def _check_gpu() -> dict:
    has_cuda = torch.cuda.is_available()
    device_count = torch.cuda.device_count() if has_cuda else 0
    device_name = torch.cuda.get_device_name(0) if has_cuda and device_count > 0 else "N/A"
    return {
        "cuda_available": has_cuda,
        "device_count": device_count,
        "primary_device": device_name,
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda if has_cuda else "N/A",
    }


def _check_yolo(config: dict[str, object]) -> dict:
    model = yolo_labels._load_model()
    available = model is not None
    model_name = str(config.get("yolo_model", "yolov8n.pt"))
    return {
        "available": available,
        "model_name": model_name,
        "confidence_threshold": float(str(config.get("yolo_confidence", 0.25))),
    }


def _check_person_backend(config: dict[str, object]) -> dict:
    preferred = str(config.get("person_backend", "auto"))
    try:
        backend = resolve_backend(preferred)
        return {
            "preferred": preferred,
            "resolved": backend.name,
            "vector_dim": backend.vector_dim,
        }
    except Exception as error:
        return {
            "preferred": preferred,
            "resolved": "error",
            "error": str(error),
        }


def run_doctor(db_path: Path | None = None) -> int:
    print("=" * 60)
    print("Fotos MVP - Diagnose")
    print("=" * 60)

    python_info = _check_python()
    print("\n[Python]")
    print(f"  Version: {python_info['version']}")
    print(f"  Executable: {python_info['executable']}")

    gpu_info = _check_gpu()
    print("\n[GPU / CUDA]")
    print(f"  CUDA available: {gpu_info['cuda_available']}")
    print(f"  Device count: {gpu_info['device_count']}")
    print(f"  Primary device: {gpu_info['primary_device']}")
    print(f"  Torch version: {gpu_info['torch_version']}")
    print(f"  CUDA version: {gpu_info['cuda_version']}")

    config: dict[str, object] = {}
    if db_path and db_path.exists():
        config = get_admin_config(db_path)

    yolo_info = _check_yolo(config)
    print("\n[YOLO (Objekt-Erkennung)]")
    print(f"  Available: {yolo_info['available']}")
    print(f"  Model name: {yolo_info['model_name']}")
    print(f"  Confidence threshold: {yolo_info['confidence_threshold']}")

    backend_info = _check_person_backend(config)
    print("\n[Personen-Backend (Embedding)]")
    print(f"  Preferred: {backend_info.get('preferred', 'N/A')}")
    print(f"  Resolved: {backend_info.get('resolved', 'N/A')}")
    print(f"  Vector dimension: {backend_info.get('vector_dim', 'N/A')}")
    if "error" in backend_info:
        print(f"  Error: {backend_info['error']}")

    print("\n[Admin-Konfiguration (SQLite)]")
    for key in (
        "yolo_model",
        "yolo_confidence",
        "person_backend",
        "person_threshold",
        "person_top_k",
        "insightface_model",
    ):
        print(f"  {key}: {config.get(key, '(default)')}")

    print("\n" + "=" * 60)
    status = "OK" if gpu_info["cuda_available"] and yolo_info["available"] else "WARNING"
    print(f"Status: {status}")
    print("=" * 60)

    return 0

