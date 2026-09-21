"""Persistent, content-addressed image cache for rotation profiles."""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid


CACHE_SCHEMA_VERSION = 2
RENDERER_CACHE_VERSION = 1
_PROFILE_ID_LENGTH = 32


def _profile_id(value) -> str:
    value = str(value).lower()
    if len(value) != _PROFILE_ID_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Profile cache IDs must be 32-character hexadecimal UUIDs.")
    return value


def _json_value(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Cache signatures cannot contain non-finite numbers.")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Cache signature keys must be text.")
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError(f"Unsupported cache signature value: {type(value).__name__}.")


def signature_digest(value) -> str:
    """Return a stable digest without persisting source URLs or credentials."""
    encoded = json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_time(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Cache source time must be text or null.")
    value = value.strip()
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise ValueError("Cache source time must be 1 to 128 printable characters.")
    return value


class ProfileImageCache:
    """Map stable profile IDs to immutable SHA-256 named PNG files."""

    def __init__(self, content_dir):
        self.content_dir = Path(content_dir).resolve()
        self.images_dir = self.content_dir / "cache"
        self.database_path = self.content_dir / "cache.sqlite3"
        self._lock = threading.RLock()
        self._verified = {}

    @contextmanager
    def _connect(self):
        self.content_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, CACHE_SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported profile cache schema version: {version}.")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_images (
                    profile_id TEXT PRIMARY KEY,
                    configuration_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    image_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    width INTEGER NOT NULL CHECK (width > 0),
                    height INTEGER NOT NULL CHECK (height > 0),
                    renderer_version INTEGER NOT NULL,
                    source_time TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(profile_images)")
            }
            if "source_time" not in columns:
                connection.execute("ALTER TABLE profile_images ADD COLUMN source_time TEXT")
            if version != CACHE_SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _image_path(self, image_hash: str) -> Path:
        return self.images_dir / image_hash[:2] / f"{image_hash}.png"

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def _valid_image(self, row) -> Path | None:
        image_hash, byte_size, width, height = row
        path = self._image_path(image_hash)
        try:
            stat = path.stat()
            marker = (stat.st_size, stat.st_mtime_ns, width, height)
            if stat.st_size != byte_size:
                return None
            if self._verified.get(image_hash) == marker:
                return path
            if self._file_hash(path) != image_hash:
                return None
            from PIL import Image
            with Image.open(path) as image:
                if image.format != "PNG" or image.size != (width, height):
                    return None
                image.verify()
            self._verified[image_hash] = marker
            return path
        except (FileNotFoundError, OSError, ValueError):
            return None

    @staticmethod
    def _upsert(connection, profile_id, configuration_hash, source_hash,
                image_hash, byte_size, width, height, source_time=None):
        source_time = _source_time(source_time)
        connection.execute(
            """
            INSERT INTO profile_images (
                profile_id, configuration_hash, source_hash, image_hash,
                byte_size, width, height, renderer_version, source_time, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                configuration_hash=excluded.configuration_hash,
                source_hash=excluded.source_hash,
                image_hash=excluded.image_hash,
                byte_size=excluded.byte_size,
                width=excluded.width,
                height=excluded.height,
                renderer_version=excluded.renderer_version,
                source_time=excluded.source_time,
                updated_at=excluded.updated_at
            """,
            (
                profile_id, configuration_hash, source_hash, image_hash,
                byte_size, width, height, RENDERER_CACHE_VERSION,
                source_time,
                dt.datetime.now(dt.timezone.utc).isoformat(),
            ),
        )

    def lookup(self, profile_id, configuration_signature, source_signature,
               output_size, source_time=None) -> Path | None:
        """Return a verified cached image and bind shared matches to the profile."""
        profile_id = _profile_id(profile_id)
        configuration_hash = signature_digest(configuration_signature)
        source_hash = signature_digest(source_signature)
        width, height = map(int, output_size)
        source_time = _source_time(source_time)
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT profile_id, image_hash, byte_size, width, height, source_time
                    FROM profile_images
                    WHERE configuration_hash=? AND source_hash=?
                      AND width=? AND height=? AND renderer_version=?
                    ORDER BY CASE WHEN profile_id=? THEN 0 ELSE 1 END, updated_at DESC
                    """,
                    (configuration_hash, source_hash, width, height,
                     RENDERER_CACHE_VERSION, profile_id),
                ).fetchall()
                for (matched_id, image_hash, byte_size, stored_width, stored_height,
                     stored_source_time) in rows:
                    path = self._valid_image(
                        (image_hash, byte_size, stored_width, stored_height)
                    )
                    if path is None:
                        connection.execute(
                            "DELETE FROM profile_images WHERE image_hash=?", (image_hash,)
                        )
                        self._verified.pop(image_hash, None)
                        continue
                    effective_source_time = source_time or stored_source_time
                    if matched_id != profile_id or effective_source_time != stored_source_time:
                        self._upsert(
                            connection, profile_id, configuration_hash, source_hash,
                            image_hash, byte_size, stored_width, stored_height,
                            effective_source_time,
                        )
                    return path
        return None

    def current(self, profile_id) -> Path | None:
        """Return the profile's verified current image regardless of source age."""
        profile_id = _profile_id(profile_id)
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT image_hash, byte_size, width, height FROM profile_images WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()
                if row is None:
                    return None
                path = self._valid_image(row)
                if path is None:
                    connection.execute("DELETE FROM profile_images WHERE profile_id=?", (profile_id,))
                return path

    def lookup_configuration(self, profile_id, configuration_signature,
                             output_size) -> Path | None:
        """Return this profile's verified image without consulting source age.

        This is used by profiles configured to download once.  Render settings
        and dimensions must still match, so changing the profile causes one new
        download instead of reusing an unrelated image.
        """
        profile_id = _profile_id(profile_id)
        configuration_hash = signature_digest(configuration_signature)
        width, height = map(int, output_size)
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT image_hash, byte_size, width, height
                    FROM profile_images
                    WHERE profile_id=? AND configuration_hash=?
                      AND width=? AND height=? AND renderer_version=?
                    """,
                    (profile_id, configuration_hash, width, height,
                     RENDERER_CACHE_VERSION),
                ).fetchone()
                if row is None:
                    return None
                path = self._valid_image(row)
                if path is None:
                    connection.execute(
                        "DELETE FROM profile_images WHERE profile_id=?", (profile_id,)
                    )
                return path

    @staticmethod
    def _validate_png(data: bytes, width: int, height: int) -> None:
        if not isinstance(data, bytes) or not data:
            raise ValueError("Cached image data must be nonempty bytes.")
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (width, height):
                raise ValueError("Cached image is not a PNG with the expected dimensions.")
            image.verify()

    def install(self, profile_id, configuration_signature, source_signature,
                data: bytes, output_size, source_time=None) -> tuple[Path, Path | None]:
        """Atomically install one immutable image and update its profile pointer."""
        profile_id = _profile_id(profile_id)
        source_time = _source_time(source_time)
        width, height = map(int, output_size)
        self._validate_png(data, width, height)
        configuration_hash = signature_digest(configuration_signature)
        source_hash = signature_digest(source_signature)
        image_hash = hashlib.sha256(data).hexdigest()
        target = self._image_path(image_hash)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target.parent / f".{image_hash}.{uuid.uuid4().hex}.tmp"
            try:
                if not target.exists() or self._file_hash(target) != image_hash:
                    with temp_path.open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, target)
                stat = target.stat()
                self._verified[image_hash] = (stat.st_size, stat.st_mtime_ns, width, height)
                with self._connect() as connection:
                    previous_row = connection.execute(
                        "SELECT image_hash, byte_size, width, height FROM profile_images WHERE profile_id=?",
                        (profile_id,),
                    ).fetchone()
                    self._upsert(
                        connection, profile_id, configuration_hash, source_hash,
                        image_hash, len(data), width, height,
                        source_time,
                    )
                previous = None
                if previous_row is not None and previous_row[0] != image_hash:
                    previous = self._valid_image(previous_row)
                return target, previous
            finally:
                temp_path.unlink(missing_ok=True)

    def entries(self, profile_ids=None) -> dict:
        """Return lightweight display metadata for present cached profile images."""
        requested = None if profile_ids is None else {_profile_id(value) for value in profile_ids}
        result = {}
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT profile_id, image_hash, byte_size, width, height,
                           source_time, updated_at
                    FROM profile_images
                    """
                ).fetchall()
                for (profile_id, image_hash, byte_size, width, height,
                     source_time, updated_at) in rows:
                    if requested is not None and profile_id not in requested:
                        continue
                    path = self._image_path(image_hash)
                    try:
                        present = path.stat().st_size == byte_size
                    except OSError:
                        present = False
                    if not present:
                        connection.execute(
                            "DELETE FROM profile_images WHERE profile_id=?", (profile_id,)
                        )
                        self._verified.pop(image_hash, None)
                        continue
                    result[profile_id] = {
                        "source_time": source_time,
                        "updated_at": updated_at,
                        "bytes": byte_size,
                        "width": width,
                        "height": height,
                        "path": str(path),
                    }
        return result

    def prune(self) -> int:
        """Remove immutable files that are no longer referenced by any profile."""
        removed = 0
        with self._lock:
            with self._connect() as connection:
                referenced = {
                    row[0] for row in connection.execute(
                        "SELECT DISTINCT image_hash FROM profile_images"
                    )
                }
            if self.images_dir.exists():
                for path in self.images_dir.glob("*/*.png"):
                    if path.is_file() and path.stem not in referenced:
                        path.unlink(missing_ok=True)
                        self._verified.pop(path.stem, None)
                        removed += 1
                for path in self.images_dir.glob("*/*.tmp"):
                    if path.is_file():
                        path.unlink(missing_ok=True)
                for folder in self.images_dir.iterdir():
                    if folder.is_dir():
                        try:
                            folder.rmdir()
                        except OSError:
                            pass
        return removed

    def retain_profiles(self, profile_ids) -> dict:
        """Drop pointers and files belonging only to deleted saved profiles."""
        retained = {_profile_id(value) for value in profile_ids}
        with self._lock:
            with self._connect() as connection:
                existing = {
                    row[0] for row in connection.execute("SELECT profile_id FROM profile_images")
                }
                removed_profiles = existing - retained
                if removed_profiles:
                    connection.executemany(
                        "DELETE FROM profile_images WHERE profile_id=?",
                        ((identifier,) for identifier in removed_profiles),
                    )
            removed_files = self.prune()
        return {"profiles": len(removed_profiles), "files": removed_files}

    def status(self) -> dict:
        """Return inexpensive cache counts and disk usage for Settings."""
        with self._lock:
            try:
                with self._connect() as connection:
                    profiles = connection.execute("SELECT COUNT(*) FROM profile_images").fetchone()[0]
            except (OSError, sqlite3.DatabaseError, RuntimeError):
                profiles = 0
            files = [] if not self.images_dir.exists() else [
                path for path in self.images_dir.glob("*/*.png") if path.is_file()
            ]
            total = 0
            for path in files:
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
            return {"profiles": profiles, "files": len(files), "bytes": total}

    def clear(self) -> dict:
        """Clear profile images and recover even when the SQLite file is corrupt."""
        with self._lock:
            before = self.status()
            for suffix in ("", "-wal", "-shm", "-journal"):
                (Path(str(self.database_path) + suffix)).unlink(missing_ok=True)
            if self.images_dir.exists():
                if self.images_dir.resolve().parent != self.content_dir:
                    raise RuntimeError("Refusing to clear a cache outside the content directory.")
                shutil.rmtree(self.images_dir)
            self._verified.clear()
            with self._connect():
                pass
            self.images_dir.mkdir(parents=True, exist_ok=True)
            return before


_CACHE_REGISTRY = {}
_CACHE_REGISTRY_LOCK = threading.Lock()


def get_profile_image_cache(content_dir) -> ProfileImageCache:
    key = str(Path(content_dir).resolve())
    with _CACHE_REGISTRY_LOCK:
        if key not in _CACHE_REGISTRY:
            _CACHE_REGISTRY[key] = ProfileImageCache(key)
        return _CACHE_REGISTRY[key]
