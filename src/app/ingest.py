from dataclasses import dataclass
from pathlib import Path
import json

from PIL import Image
from PIL.ExifTags import TAGS


@dataclass(frozen=True)
class ExifData:
    taken_ts: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    orientation: int | None = None  # 1-8
    camera_model: str | None = None
    iso: int | None = None
    f_number: float | None = None
    shutter_speed: str | None = None
    focal_length: float | None = None
    flash: str | None = None
    creator: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    size_bytes: int
    modified_ts: float
    taken_ts: float | None = None  # EXIF DateTimeOriginal als timestamp
    exif_data: ExifData | None = None


def _gps_to_decimal(gps_data: dict) -> tuple[float, float] | None:
    """Konvertiert GPS-Daten vom EXIF-Format in Dezimalgrad."""
    try:
        lat_ref = gps_data.get(1, "N")  # GPSLatitudeRef
        lon_ref = gps_data.get(3, "E")  # GPSLongitudeRef
        lat = gps_data.get(2)  # GPSLatitude
        lon = gps_data.get(4)  # GPSLongitude

        if not (lat and lon):
            return None

        # Konvertiere von (degrees, minutes, seconds) zu Dezimalgrad
        lat_decimal = lat[0] + lat[1] / 60 + lat[2] / 3600
        lon_decimal = lon[0] + lon[1] / 60 + lon[2] / 3600

        # Wende Richtung an
        if lat_ref == "S":
            lat_decimal = -lat_decimal
        if lon_ref == "W":
            lon_decimal = -lon_decimal

        return lat_decimal, lon_decimal
    except (TypeError, KeyError, ZeroDivisionError):
        return None


def _extract_exif_data(image_path: Path) -> ExifData:
    """Extrahiert umfassende EXIF-Daten aus einem Bild."""
    try:
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                return ExifData()

            data = {}

            # DateTime (36867 = DateTimeOriginal)
            for tag_id, value in exif_data.items():
                if tag_id == 36867:  # DateTimeOriginal
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        data["taken_ts"] = dt.timestamp()
                    except (ValueError, TypeError):
                        pass

                # Orientierung (274)
                elif tag_id == 274:  # Orientation
                    try:
                        data["orientation"] = int(value)
                    except (ValueError, TypeError):
                        pass

                # Kameramodell (271 = Make, 272 = Model)
                elif tag_id == 272:  # Model
                    data["camera_model"] = str(value)

                # ISO (34855)
                elif tag_id == 34855:  # ISOSpeedRatings
                    try:
                        data["iso"] = int(value if isinstance(value, (int, str)) else value[0])
                    except (ValueError, TypeError, IndexError):
                        pass

                # Blendenzahl (33437)
                elif tag_id == 33437:  # FNumber
                    try:
                        fnum = value
                        if hasattr(fnum, 'numerator') and hasattr(fnum, 'denominator'):
                            data["f_number"] = float(fnum.numerator) / float(fnum.denominator)
                        else:
                            data["f_number"] = float(fnum)
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

                # Verschlusszeit (33434)
                elif tag_id == 33434:  # ExposureTime
                    try:
                        exp_time = value
                        if hasattr(exp_time, 'numerator') and hasattr(exp_time, 'denominator'):
                            seconds = float(exp_time.numerator) / float(exp_time.denominator)
                            # Formatiere als Bruch z.B. "1/125"
                            if seconds < 1:
                                data["shutter_speed"] = f"1/{int(1/seconds)}"
                            else:
                                data["shutter_speed"] = f"{seconds:.1f}s"
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

                # Brennweite (37386)
                elif tag_id == 37386:  # FocalLength
                    try:
                        focal = value
                        if hasattr(focal, 'numerator') and hasattr(focal, 'denominator'):
                            data["focal_length"] = float(focal.numerator) / float(focal.denominator)
                        else:
                            data["focal_length"] = float(focal)
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

                # Blitz (37385)
                elif tag_id == 37385:  # Flash
                    try:
                        flash_value = int(value)
                        if flash_value & 1:
                            data["flash"] = "on"
                        else:
                            data["flash"] = "off"
                    except (ValueError, TypeError):
                        pass

                # Artist/Creator (315)
                elif tag_id == 315:  # Artist
                    data["creator"] = str(value)

                # Bildbeschreibung (270)
                elif tag_id == 270:  # ImageDescription
                    data["description"] = str(value)

            # GPS-Daten (34853 = GPSInfo)
            if 34853 in exif_data:
                gps_info = exif_data[34853]
                gps_result = _gps_to_decimal(gps_info)
                if gps_result:
                    data["latitude"], data["longitude"] = gps_result

                # Höhe (6 = GPSAltitude)
                if 6 in gps_info:
                    try:
                        alt = gps_info[6]
                        if hasattr(alt, 'numerator') and hasattr(alt, 'denominator'):
                            data["altitude"] = float(alt.numerator) / float(alt.denominator)
                        else:
                            data["altitude"] = float(alt)
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

            return ExifData(**{k: v for k, v in data.items() if k in ExifData.__dataclass_fields__})
    except Exception:
        pass

    return ExifData()


def scan_images(root: Path, supported_extensions: tuple[str, ...]) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    if not root.exists():
        return records

    extensions = set(ext.lower() for ext in supported_extensions)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue

        stat = path.stat()
        exif_data = _extract_exif_data(path)
        records.append(
            ImageRecord(
                path=path,
                size_bytes=stat.st_size,
                modified_ts=stat.st_mtime,
                taken_ts=exif_data.taken_ts,
                exif_data=exif_data,
            )
        )

    return records

