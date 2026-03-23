"""Diagnose-Tools fuer das Foto-MVP."""

import os
import sys

import torch

from .detectors import labels as yolo_labels
from .persons.embeddings import resolve_backend


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


def _check_yolo() -> dict:
    model = yolo_labels._load_model()
    available = model is not None
    model_name = os.getenv("FOTOS_YOLO_MODEL", "yolov8n.pt")
    return {
        "available": available,
        "model_name": model_name,
        "confidence_threshold": float(os.getenv("FOTOS_YOLO_CONF", "0.25")),
    }


def _check_person_backend() -> dict:
    preferred = os.getenv("FOTOS_PERSON_BACKEND", "auto")
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


def run_doctor() -> int:
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

    yolo_info = _check_yolo()
    print("\n[YOLO (Objekt-Erkennung)]")
    print(f"  Available: {yolo_info['available']}")
    print(f"  Model name: {yolo_info['model_name']}")
    print(f"  Confidence threshold: {yolo_info['confidence_threshold']}")

    backend_info = _check_person_backend()
    print("\n[Personen-Backend (Embedding)]")
    print(f"  Preferred: {backend_info.get('preferred', 'N/A')}")
    print(f"  Resolved: {backend_info.get('resolved', 'N/A')}")
    print(f"  Vector dimension: {backend_info.get('vector_dim', 'N/A')}")
    if "error" in backend_info:
        print(f"  Error: {backend_info['error']}")

    print("\n[Umgebungsvariablen]")
    env_vars = {
        "FOTOS_YOLO_MODEL": "yolov8n.pt",
        "FOTOS_YOLO_CONF": "0.25",
        "FOTOS_PERSON_THRESHOLD": "0.90",
        "FOTOS_PERSON_TOP_K": "3",
        "FOTOS_PERSON_BACKEND": "auto",
        "FOTOS_INSIGHTFACE_MODEL": "buffalo_l",
    }
    for var, default_val in env_vars.items():
        current_val = os.getenv(var, f"(default: {default_val})")
        print(f"  {var}: {current_val}")

    print("\n" + "=" * 60)
    status = "OK" if gpu_info["cuda_available"] and yolo_info["available"] else "WARNING"
    print(f"Status: {status}")
    print("=" * 60)

    return 0

