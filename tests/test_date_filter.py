#!/usr/bin/env python3
"""Test script for date filtering functionality"""

import sys
import tempfile
from pathlib import Path
from datetime import datetime
import sqlite3
import json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app.index.store import (
    ensure_schema,
    upsert_photo,
    search_photos_page,
    _parse_date_filters
)
from src.app.ingest import ImageRecord, ExifData
from PIL import Image

# Test _parse_date_filters function
print("Testing _parse_date_filters...")
query1 = "hund month:03 year:2023"
terms1, filters1 = _parse_date_filters(query1)
print(f"Query: '{query1}'")
print(f"Terms: {terms1}")
print(f"Filters: {filters1}")
print()

query2 = "month:6 person:max"
terms2, filters2 = _parse_date_filters(query2)
print(f"Query: '{query2}'")
print(f"Terms: {terms2}")
print(f"Filters: {filters2}")
print()

query3 = "year:2022"
terms3, filters3 = _parse_date_filters(query3)
print(f"Query: '{query3}'")
print(f"Terms: {terms3}")
print(f"Filters: {filters3}")
print()

# Test with actual database
print("Testing with database...")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
    workspace = Path(tmp_dir)
    db_path = workspace / "data" / "photo_index.db"
    photos_dir = workspace / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    ensure_schema(db_path)

    # Create test images with EXIF data
    # Photo 1: March 2023
    image1_path = photos_dir / "photo_march_2023.jpg"
    image = Image.new("RGB", (64, 64), color=(150, 60, 90))
    image.save(image1_path)
    stat = image1_path.stat()

    march_2023_ts = datetime(2023, 3, 15, 10, 30, 0).timestamp()

    exif1 = ExifData(taken_ts=march_2023_ts)
    record1 = ImageRecord(
        path=image1_path,
        size_bytes=stat.st_size,
        modified_ts=stat.st_mtime,
        taken_ts=march_2023_ts,
        exif_data=exif1
    )

    upsert_photo(db_path=db_path, record=record1, labels=["test", "dog"])

    # Photo 2: June 2023
    image2_path = photos_dir / "photo_june_2023.jpg"
    image.save(image2_path)
    stat = image2_path.stat()

    june_2023_ts = datetime(2023, 6, 20, 14, 45, 0).timestamp()

    exif2 = ExifData(taken_ts=june_2023_ts)
    record2 = ImageRecord(
        path=image2_path,
        size_bytes=stat.st_size,
        modified_ts=stat.st_mtime,
        taken_ts=june_2023_ts,
        exif_data=exif2
    )

    upsert_photo(db_path=db_path, record=record2, labels=["test", "photo"])

    # Photo 3: March 2022
    image3_path = photos_dir / "photo_march_2022.jpg"
    image.save(image3_path)
    stat = image3_path.stat()

    march_2022_ts = datetime(2022, 3, 10, 12, 0, 0).timestamp()

    exif3 = ExifData(taken_ts=march_2022_ts)
    record3 = ImageRecord(
        path=image3_path,
        size_bytes=stat.st_size,
        modified_ts=stat.st_mtime,
        taken_ts=march_2022_ts,
        exif_data=exif3
    )

    upsert_photo(db_path=db_path, record=record3, labels=["test", "old"])

    # Test search by month and year
    print("\nSearch for 'month:03 year:2023' (March 2023):")
    results, total = search_photos_page(db_path=db_path, query="month:03 year:2023", limit=20)
    print(f"Found {total} photos")
    for result in results:
        path_obj = Path(result.path) if isinstance(result.path, str) else result.path
        print(f"  - {path_obj.name}: {result.labels}")

    print("\nSearch for 'month:6' (June of any year):")
    results, total = search_photos_page(db_path=db_path, query="month:6", limit=20)
    print(f"Found {total} photos")
    for result in results:
        path_obj = Path(result.path) if isinstance(result.path, str) else result.path
        print(f"  - {path_obj.name}: {result.labels}")

    print("\nSearch for 'year:2022' (All 2022):")
    results, total = search_photos_page(db_path=db_path, query="year:2022", limit=20)
    print(f"Found {total} photos")
    for result in results:
        path_obj = Path(result.path) if isinstance(result.path, str) else result.path
        print(f"  - {path_obj.name}: {result.labels}")

    print("\nSearch for 'test month:03' (March of any year with 'test'):")
    results, total = search_photos_page(db_path=db_path, query="test month:03", limit=20)
    print(f"Found {total} photos")
    for result in results:
        path_obj = Path(result.path) if isinstance(result.path, str) else result.path
        print(f"  - {path_obj.name}: {result.labels}")

print("\n✓ Date filter tests completed successfully!")

