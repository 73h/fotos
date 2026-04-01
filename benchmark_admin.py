#!/usr/bin/env python
"""
Performance Benchmark Tool für Admin-Dashboard.
Misst und vergleicht Job-Ausführungszeiten.
"""

import time
import tempfile
from pathlib import Path
from PIL import Image

from src.app.config import AppConfig
from src.app.web.admin_jobs import get_job_manager, JobStatus
from src.app.web.admin_service import AdminService


def create_sample_images(count: int, directory: Path) -> list[Path]:
    """Erstelle Test-Bilder"""
    paths = []
    for i in range(count):
        img = Image.new("RGB", (1920, 1080), color=(100 + i % 156, 100, 100))
        path = directory / f"test_image_{i:04d}.jpg"
        img.save(path, quality=85)
        paths.append(path)
    return paths


def benchmark_job_manager():
    """Benchmark JobManager-Operationen"""
    print("\n" + "=" * 70)
    print("BENCHMARK: Job-Manager Operationen")
    print("=" * 70)

    manager = get_job_manager()

    # Test 1: Job Creation Performance
    print("\n1. Job Creation (100 jobs):")
    start = time.time()
    for i in range(100):
        manager.create_job(f"bench_job_{i}", "full_index", total=1000)
    elapsed = time.time() - start
    print(f"   Time: {elapsed:.4f}s")
    print(f"   Per job: {elapsed/100*1000:.2f}ms")

    # Test 2: Progress Updates Performance
    print("\n2. Progress Updates (1000 updates):")
    job_id = "bench_job_0"
    start = time.time()
    for i in range(1000):
        manager.update_progress(job_id, i, 1000, f"Update {i}")
    elapsed = time.time() - start
    print(f"   Time: {elapsed:.4f}s")
    print(f"   Per update: {elapsed/1000*1000:.3f}ms")

    # Test 3: Status Transitions
    print("\n3. Status Transitions (1000 reads + transitions):")
    start = time.time()
    for i in range(100):
        manager.set_job_running(f"bench_job_{i}")
        manager.get_job(f"bench_job_{i}")
        manager.request_abort(f"bench_job_{i}")
    elapsed = time.time() - start
    print(f"   Time: {elapsed:.4f}s")
    print(f"   Per transition: {elapsed/100*1000:.3f}ms")

    # Test 4: Serialization
    print("\n4. Job Serialization (100 jobs to dict):")
    start = time.time()
    for i in range(100):
        job = manager.get_job(f"bench_job_{i}")
        if job:
            job.to_dict()
    elapsed = time.time() - start
    print(f"   Time: {elapsed:.4f}s")
    print(f"   Per job: {elapsed/100*1000:.3f}ms")

    # Test 5: Cleanup Performance
    print("\n5. Cleanup Old Jobs:")
    start = time.time()
    manager.cleanup_old_jobs(max_age_seconds=0)
    elapsed = time.time() - start
    print(f"   Time: {elapsed:.4f}s")
    print(f"   Removed: {len(manager.get_all_jobs())} jobs remaining")

    print("\n✅ Job-Manager Benchmarks complete!")


def benchmark_admin_service():
    """Benchmark AdminService mit kleinen Bildern"""
    print("\n" + "=" * 70)
    print("BENCHMARK: Admin-Service Operationen")
    print("=" * 70)

    workspace = Path.cwd()
    config = AppConfig.from_workspace(workspace)
    manager = get_job_manager()
    service = AdminService(config, manager)

    # Erstelle Sample-Bilder
    with tempfile.TemporaryDirectory() as tmpdir:
        photo_dir = Path(tmpdir)
        print(f"\n📷 Creating 50 sample images in {photo_dir}...")

        start = time.time()
        images = create_sample_images(50, photo_dir)
        creation_time = time.time() - start
        print(f"   ✓ Created in {creation_time:.2f}s")

        # Test Full-Index
        print(f"\n1. Full-Index (50 images, 1 worker):")
        job_id = service.start_full_index(
            photo_roots=[str(photo_dir)],
            person_backend="histogram",  # Schneller für Test
            force_reindex=True,
            index_workers=1,
            near_duplicates=False,
        )

        start = time.time()
        while True:
            job = manager.get_job(job_id)
            if job.status != JobStatus.RUNNING:
                break
            time.sleep(0.1)
        elapsed = time.time() - start

        print(f"   Status: {job.status}")
        print(f"   Time: {elapsed:.2f}s")
        print(f"   Per image: {elapsed/50*1000:.1f}ms")
        print(f"   Message: {job.message}")

        # Test EXIF-Update
        print(f"\n2. EXIF-Update (50 images):")
        job_id = service.start_exif_update()

        start = time.time()
        while True:
            job = manager.get_job(job_id)
            if job.status != JobStatus.RUNNING:
                break
            time.sleep(0.1)
        elapsed = time.time() - start

        print(f"   Status: {job.status}")
        print(f"   Time: {elapsed:.3f}s")
        print(f"   Per image: {elapsed/50*1000:.2f}ms")

        # Test Rematch (wenn DB nicht leer)
        print(f"\n3. Rematch-Persons (1 worker):")
        job_id = service.start_rematch_persons(
            person_backend="histogram",
            workers=1,
        )

        start = time.time()
        while True:
            job = manager.get_job(job_id)
            if job.status != JobStatus.RUNNING:
                break
            time.sleep(0.1)
        elapsed = time.time() - start

        print(f"   Status: {job.status}")
        print(f"   Time: {elapsed:.3f}s")
        print(f"   Per image: {elapsed/50*1000:.2f}ms" if elapsed > 0 else "   No images to match")

    print("\n✅ Admin-Service Benchmarks complete!")


def print_summary():
    """Zeige Zusammenfassung"""
    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)

    print("""
    Typical Performance Expectations:
    
    Job-Manager:
    ✓ Job creation:    0.1-0.5ms per job
    ✓ Progress update: 0.01-0.05ms per update
    ✓ Status change:   0.01-0.1ms per transition
    ✓ Serialization:   0.1-0.5ms per job
    
    Full-Index:
    ✓ Histogram Backend: 2-5ms per image
    ✓ InsightFace:       10-50ms per image (with GPU)
    ✓ Label detection:   5-15ms per image (YOLO)
    ✓ Skip logic:        0.1ms per image (fast skip)
    
    EXIF-Update:
    ✓ Per image:       1-2ms
    ✓ Very fast!
    
    Rematch-Persons:
    ✓ Histogram:       2-5ms per image
    ✓ InsightFace:     10-20ms per image
    
    Scaling with Workers:
    ✓ 1 Worker:   Baseline (sequential)
    ✓ 4 Workers:  2.5-3.5x faster
    ✓ 8 Workers:  4-6x faster (with enough RAM)
    """)

    print("=" * 70)


if __name__ == "__main__":
    print("🏃 Running Admin-Dashboard Performance Benchmarks...")

    try:
        benchmark_job_manager()
        benchmark_admin_service()
        print_summary()
        print("\n✅ All benchmarks completed successfully!")
    except Exception as e:
        print(f"\n❌ Benchmark error: {e}")
        import traceback
        traceback.print_exc()

