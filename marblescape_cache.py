"""Persistent, content-addressed image cache for rotation profiles.

Each profile keeps several variants: one picture per distinct render
configuration and size, so switching back to recently used settings needs no
download. A newer source frame replaces the variant of the same settings. The
most recently used variant is the profile's current picture. Limits on the
variants per profile and on the total size drop the least recently used
variants first; each profile's current picture always stays.
"""

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


CACHE_SCHEMA_VERSION = 3
RENDERER_CACHE_VERSION = 1
_PROFILE_ID_LENGTH = 32
# Limits until the application sets its own (History & Storage).
DEFAULT_MAX_VARIANTS = 5
DEFAULT_MAX_BYTES = 2 * 1024 ** 3
_COLUMNS = ("profile_id", "configuration_hash", "width", "height", "source_hash",
            "image_hash", "byte_size", "renderer_version", "source_time",
            "updated_at", "used_at")


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
        self._last_stamp = ""
        self.max_variants = DEFAULT_MAX_VARIANTS
        self.max_bytes = DEFAULT_MAX_BYTES
        self.unlimited_variants = frozenset()
        self.separate_limits = {}

    def set_limits(self, max_bytes, max_variants, unlimited_variants=(), separate_limits=None):
        """Set the size limit in bytes and the variants kept per profile.

        Profiles in ``unlimited_variants`` keep any number of variants; only
        the size limit applies to them. Profiles in ``separate_limits``
        (profile id -> bytes) have a size budget of their own and do not count
        toward ``max_bytes``, so they can never push out the others' pictures.
        """
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("The profile cache size limit must be a positive number of bytes.")
        if type(max_variants) is not int or max_variants < 1:
            raise ValueError("Profile cache variants per profile must be at least 1.")
        unlimited = frozenset(_profile_id(value) for value in unlimited_variants)
        separate = {}
        for profile_id, limit in (separate_limits or {}).items():
            if type(limit) is not int or limit <= 0:
                raise ValueError("A separate profile cache size limit must be a positive number of bytes.")
            separate[_profile_id(profile_id)] = limit
        with self._lock:
            self.max_bytes = max_bytes
            self.max_variants = max_variants
            self.unlimited_variants = unlimited
            self.separate_limits = separate

    def _stamp(self) -> str:
        """A UTC time that never repeats within this process, to order variants by use."""
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        if stamp <= self._last_stamp:
            last = dt.datetime.fromisoformat(self._last_stamp)
            stamp = (last + dt.timedelta(microseconds=1)).isoformat(timespec="microseconds")
        self._last_stamp = stamp
        return stamp

    @contextmanager
    def _connect(self):
        self.content_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        try:
            connection.execute("PRAGMA synchronous = FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, CACHE_SCHEMA_VERSION):
                raise RuntimeError(f"Unsupported profile cache schema version: {version}.")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(profile_images)")
            }
            if columns and "used_at" not in columns:
                # Schemas 1-2 kept one picture per profile; it becomes the first variant.
                # One explicit transaction, so an interrupted migration leaves the old table.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ALTER TABLE profile_images RENAME TO profile_images_single")
                self._create_table(connection)
                source_time = "source_time" if "source_time" in columns else "NULL"
                connection.execute(
                    f"""
                    INSERT INTO profile_images ({", ".join(_COLUMNS)})
                    SELECT profile_id, configuration_hash, width, height, source_hash,
                           image_hash, byte_size, renderer_version, {source_time},
                           updated_at, updated_at
                    FROM profile_images_single
                    """
                )
                connection.execute("DROP TABLE profile_images_single")
            else:
                self._create_table(connection)
            if version != CACHE_SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _create_table(connection):
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_images (
                profile_id TEXT NOT NULL,
                configuration_hash TEXT NOT NULL,
                width INTEGER NOT NULL CHECK (width > 0),
                height INTEGER NOT NULL CHECK (height > 0),
                source_hash TEXT NOT NULL,
                image_hash TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                renderer_version INTEGER NOT NULL,
                source_time TEXT,
                updated_at TEXT NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY (profile_id, configuration_hash, width, height)
            )
            """
        )

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
            from marblescape_image_naming import has_no_image_data
            if has_no_image_data(path):
                return None  # A picture without image data is dropped, not shown again.
            self._verified[image_hash] = marker
            return path
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _upsert(self, connection, profile_id, configuration_hash, source_hash,
                image_hash, byte_size, width, height, source_time=None):
        """Store the profile's variant for these settings and size and mark it used."""
        source_time = _source_time(source_time)
        stamp = self._stamp()
        connection.execute(
            """
            INSERT INTO profile_images (
                profile_id, configuration_hash, width, height, source_hash, image_hash,
                byte_size, renderer_version, source_time, updated_at, used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, configuration_hash, width, height) DO UPDATE SET
                source_hash=excluded.source_hash,
                image_hash=excluded.image_hash,
                byte_size=excluded.byte_size,
                renderer_version=excluded.renderer_version,
                source_time=excluded.source_time,
                updated_at=excluded.updated_at,
                used_at=excluded.used_at
            """,
            (
                profile_id, configuration_hash, width, height, source_hash, image_hash,
                byte_size, RENDERER_CACHE_VERSION, source_time, stamp, stamp,
            ),
        )

    def _touch(self, connection, profile_id, configuration_hash, width, height):
        """Mark a variant as just used, so it becomes the profile's current picture."""
        connection.execute(
            """
            UPDATE profile_images SET used_at=?
            WHERE profile_id=? AND configuration_hash=? AND width=? AND height=?
            """,
            (self._stamp(), profile_id, configuration_hash, width, height),
        )

    @staticmethod
    def _delete_variant(connection, profile_id, configuration_hash, width, height):
        connection.execute(
            """
            DELETE FROM profile_images
            WHERE profile_id=? AND configuration_hash=? AND width=? AND height=?
            """,
            (profile_id, configuration_hash, width, height),
        )

    def lookup(self, profile_id, configuration_signature, source_signature,
               output_size, source_time=None) -> Path | None:
        """Return a verified cached image and bind shared matches to the profile."""
        profile_id = _profile_id(profile_id)
        configuration_hash = signature_digest(configuration_signature)
        source_hash = signature_digest(source_signature)
        width, height = map(int, output_size)
        source_time = _source_time(source_time)
        bound = False
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT profile_id, image_hash, byte_size, width, height, source_time
                    FROM profile_images
                    WHERE configuration_hash=? AND source_hash=?
                      AND width=? AND height=? AND renderer_version=?
                    ORDER BY CASE WHEN profile_id=? THEN 0 ELSE 1 END, used_at DESC
                    """,
                    (configuration_hash, source_hash, width, height,
                     RENDERER_CACHE_VERSION, profile_id),
                ).fetchall()
                found = None
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
                        bound = matched_id != profile_id
                    else:
                        self._touch(connection, profile_id, configuration_hash, width, height)
                    found = path
                    break
            if bound:
                # A new variant of this profile may exceed the limits; the shared
                # file stays referenced, so nothing on disk is lost.
                self.enforce_limits()
        return found

    def current(self, profile_id) -> Path | None:
        """Return the profile's most recently used verified picture, regardless of source age."""
        profile_id = _profile_id(profile_id)
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT configuration_hash, width, height, image_hash, byte_size
                    FROM profile_images WHERE profile_id=?
                    ORDER BY used_at DESC
                    """,
                    (profile_id,),
                ).fetchall()
                for configuration_hash, width, height, image_hash, byte_size in rows:
                    path = self._valid_image((image_hash, byte_size, width, height))
                    if path is not None:
                        return path
                    self._delete_variant(connection, profile_id, configuration_hash, width, height)
                return None

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
                    self._delete_variant(connection, profile_id, configuration_hash, width, height)
                else:
                    self._touch(connection, profile_id, configuration_hash, width, height)
                return path

    def stored_source_time(self, profile_id, configuration_signature,
                           output_size) -> str | None:
        """Return the acquisition time of this profile's verified image, if known.

        Render settings and dimensions must match, as in ``lookup_configuration``.
        """
        profile_id = _profile_id(profile_id)
        configuration_hash = signature_digest(configuration_signature)
        width, height = map(int, output_size)
        with self._lock:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT image_hash, byte_size, width, height, source_time
                    FROM profile_images
                    WHERE profile_id=? AND configuration_hash=?
                      AND width=? AND height=? AND renderer_version=?
                    """,
                    (profile_id, configuration_hash, width, height,
                     RENDERER_CACHE_VERSION),
                ).fetchone()
                if row is None or self._valid_image(row[:4]) is None:
                    return None
                return row[4]

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
        """Atomically install one immutable image as the profile's current variant.

        Returns the new path and the profile's previous current picture when it
        differs, so the caller can archive it. Variants over the limits lose
        their database entries here; their files stay until ``prune``, which
        callers run after archiving.
        """
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
                from marblescape_image_naming import has_no_image_data
                if not has_no_image_data(target):
                    # A picture without image data stays unverified: lookups drop it.
                    self._verified[image_hash] = (stat.st_size, stat.st_mtime_ns, width, height)
                with self._connect() as connection:
                    previous_row = connection.execute(
                        """
                        SELECT image_hash, byte_size, width, height FROM profile_images
                        WHERE profile_id=? ORDER BY used_at DESC LIMIT 1
                        """,
                        (profile_id,),
                    ).fetchone()
                    self._upsert(
                        connection, profile_id, configuration_hash, source_hash,
                        image_hash, len(data), width, height,
                        source_time,
                    )
                self.enforce_limits(prune=False)
                previous = None
                if previous_row is not None and previous_row[0] != image_hash:
                    previous = self._valid_image(previous_row)
                return target, previous
            finally:
                temp_path.unlink(missing_ok=True)

    def entries(self, profile_ids=None) -> dict:
        """Return display metadata of each profile's current picture and its variant count."""
        requested = None if profile_ids is None else {_profile_id(value) for value in profile_ids}
        result = {}
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT profile_id, configuration_hash, image_hash, byte_size, width, height,
                           source_time, updated_at
                    FROM profile_images
                    ORDER BY used_at DESC
                    """
                ).fetchall()
                for (profile_id, configuration_hash, image_hash, byte_size, width, height,
                     source_time, updated_at) in rows:
                    if requested is not None and profile_id not in requested:
                        continue
                    path = self._image_path(image_hash)
                    try:
                        present = path.stat().st_size == byte_size
                    except OSError:
                        present = False
                    if not present:
                        self._delete_variant(connection, profile_id, configuration_hash, width, height)
                        self._verified.pop(image_hash, None)
                        continue
                    if profile_id in result:
                        result[profile_id]["variants"] += 1
                        continue
                    result[profile_id] = {
                        "source_time": source_time,
                        "updated_at": updated_at,
                        "bytes": byte_size,
                        "width": width,
                        "height": height,
                        "path": str(path),
                        "variants": 1,
                    }
        return result

    def enforce_limits(self, prune=True) -> int:
        """Drop the least recently used variants beyond the limits; return how many.

        First each profile keeps at most ``max_variants`` (except those in
        ``unlimited_variants``); then, while the distinct files exceed
        ``max_bytes``, the oldest variants that are not a profile's current
        picture go. Files shared by several variants count once. Profiles with
        a separate limit are trimmed the same way against their own budget.
        """
        removed = []
        with self._lock:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT profile_id, configuration_hash, width, height, image_hash, byte_size
                    FROM profile_images ORDER BY used_at DESC
                    """
                ).fetchall()
                kept, counts = [], {}
                for row in rows:
                    counts[row[0]] = counts.get(row[0], 0) + 1
                    within = counts[row[0]] <= self.max_variants or row[0] in self.unlimited_variants
                    (kept if within else removed).append(row)
                groups = {None: []}
                for row in kept:
                    groups.setdefault(row[0] if row[0] in self.separate_limits else None, []).append(row)
                for group, group_rows in groups.items():
                    limit = self.max_bytes if group is None else self.separate_limits[group]
                    removed.extend(self._over_budget(group_rows, limit))
                for profile_id, configuration_hash, width, height, _image, _size in removed:
                    self._delete_variant(connection, profile_id, configuration_hash, width, height)
            if removed and prune:
                self.prune()
        return len(removed)

    @staticmethod
    def _over_budget(rows, limit):
        """The least recently used ``rows`` to drop so their distinct files fit ``limit``."""
        references, sizes = {}, {}
        for row in rows:
            references[row[4]] = references.get(row[4], 0) + 1
            sizes[row[4]] = row[5]
        total = sum(sizes.values())
        current, current_images = set(), set()
        candidates = []
        for row in rows:
            if row[0] in current:
                candidates.append(row)
            else:
                current.add(row[0])
                current_images.add(row[4])
        # A variant sharing a current picture's file would free nothing.
        candidates = [row for row in candidates if row[4] not in current_images]
        removed = []
        for row in reversed(candidates):  # Least recently used first.
            if total <= limit:
                break
            removed.append(row)
            references[row[4]] -= 1
            if references[row[4]] == 0:
                total -= sizes[row[4]]
        return removed

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
        """Return inexpensive cache counts and disk usage for Settings.

        ``files`` and ``bytes`` count the whole cache on disk. ``profiles`` and
        ``variants`` leave out the profiles with a separate limit; with such
        limits, ``main`` and ``separate`` hold the files and bytes per budget.
        """
        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        "SELECT profile_id, image_hash, byte_size FROM profile_images"
                    ).fetchall()
            except (OSError, sqlite3.DatabaseError, RuntimeError):
                rows = []

            def usage(group):
                files = {row[1]: row[2] for row in group}
                return {"variants": len(group), "files": len(files), "bytes": sum(files.values())}

            main = [row for row in rows if row[0] not in self.separate_limits]
            files_on_disk = [] if not self.images_dir.exists() else [
                path for path in self.images_dir.glob("*/*.png") if path.is_file()
            ]
            total = 0
            for path in files_on_disk:
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
            result = {"profiles": len({row[0] for row in main}), "variants": len(main),
                      "files": len(files_on_disk), "bytes": total}
            if self.separate_limits:
                result["main"] = usage(main)
                result["separate"] = {
                    profile_id: usage([row for row in rows if row[0] == profile_id])
                    for profile_id in self.separate_limits
                }
            return result

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
