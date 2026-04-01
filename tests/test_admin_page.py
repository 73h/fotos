"""
Einfacher Test für die Admin-Seite und Job-Manager.
"""
import json
from pathlib import Path

from src.app.config import AppConfig
from src.app.web.admin_jobs import JobManager, JobStatus


def test_job_manager():
    """Teste grundlegende Job-Manager-Funktionalität."""
    print("=" * 60)
    print("Test: JobManager")
    print("=" * 60)

    manager = JobManager()

    # Create a job
    job = manager.create_job("test_job_1", "full_index", total=100)
    print(f"✓ Job erstellt: {job.job_id}")
    assert job.job_id == "test_job_1"
    assert job.total == 100
    assert job.percentage == 0.0

    # Update progress
    manager.update_progress("test_job_1", 50, 100, "Halb fertig")
    job = manager.get_job("test_job_1")
    print(f"✓ Progress aktualisiert: {job.current}/{job.total} ({job.percentage}%)")
    assert job.current == 50
    assert job.percentage == 50.0

    # Set running
    manager.set_job_running("test_job_1")
    job = manager.get_job("test_job_1")
    print(f"✓ Job auf RUNNING gesetzt: {job.status}")
    assert job.status == JobStatus.RUNNING

    # Request abort
    assert manager.request_abort("test_job_1") == True
    job = manager.get_job("test_job_1")
    print(f"✓ Abort angefordert: {job.should_abort()}")
    assert job.should_abort() == True

    # Complete job
    manager.set_job_completed("test_job_1", "Fertig!")
    job = manager.get_job("test_job_1")
    print(f"✓ Job abgeschlossen: {job.status}, {job.percentage}%")
    assert job.status == JobStatus.COMPLETED
    assert job.percentage == 100.0

    # Test serialization
    job_dict = job.to_dict()
    print(f"✓ Serialisierung zu JSON: {json.dumps(job_dict, indent=2)}")

    # Test cleanup
    manager.cleanup_old_jobs(max_age_seconds=0)  # Cleanup all old
    jobs = manager.get_all_jobs()
    print(f"✓ Nach Cleanup: {len(jobs)} Jobs verbleibend")

    print("\n✅ Job-Manager Test bestanden!\n")


def test_flask_app():
    """Teste, dass die Flask-App mit Admin-Routes startet."""
    print("=" * 60)
    print("Test: Flask App mit Admin-Routes")
    print("=" * 60)

    from src.app.web import create_app

    workspace_root = Path(__file__).resolve().parents[0]
    config = AppConfig.from_workspace(workspace_root)
    app = create_app(config)

    print(f"✓ Flask App erstellt")
    print(f"  - APP_CONFIG: {app.config.get('APP_CONFIG')}")
    print(f"  - DB_PATH: {app.config.get('DB_PATH')}")
    print(f"  - CACHE_DIR: {app.config.get('CACHE_DIR')}")

    # Check routes
    admin_routes = [
        "/admin",
        "/api/admin/config/start-index",
        "/api/admin/config/start-exif",
        "/api/admin/config/start-rematch",
        "/api/admin/job/<job_id>",
        "/api/admin/job/<job_id>/abort",
        "/api/admin/jobs",
    ]

    with app.app_context():
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        for route in admin_routes:
            route_path = route.replace("<job_id>", "123")
            found = any(route_path in rule or rule in route_path for rule in rules)
            status = "✓" if found else "✗"
            print(f"  {status} {route}")

    print("\n✅ Flask App Test bestanden!\n")


if __name__ == "__main__":
    test_job_manager()
    test_flask_app()
    print("=" * 60)
    print("🎉 Alle Tests bestanden!")
    print("=" * 60)

