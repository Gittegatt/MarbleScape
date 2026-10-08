"""Local, independently remembered settings/profile export destinations."""

import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading


def settings_backup_filename(include_profiles, when=None):
    scope = "settings-profiles" if include_profiles else "settings"
    return f"marblescape-{scope}-{when or dt.datetime.now():%Y-%m-%d_%H%M%S}.json"


class ExportLocations:
    def __init__(self, application_dir):
        application_dir = Path(application_dir).resolve()
        self.root = application_dir / "export"
        self.state_path = self.root / "export_locations.json"
        self._lock = threading.RLock()
        self._locations = {}
        try:
            legacy = application_dir / "backups" / "export_locations.json"
            with (self.state_path if self.state_path.exists() else legacy).open("rb") as handle:
                data = handle.read(16385)
            if len(data) <= 16384:
                state = json.loads(data)
                if isinstance(state, dict):
                    self._locations = {key: value for key, value in state.items()
                                       if key in {"settings", "profiles"} and isinstance(value, str)}
                    for category, value in self._locations.items():
                        if Path(value) == application_dir / "backups" / category:
                            self._locations[category] = str(self.root / category)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _available(path):
        try:
            if not path.is_absolute() or not path.is_dir():
                return False
            # Test actual access, including Windows ACLs and disconnected drives.
            with tempfile.TemporaryFile(dir=path, prefix=".marblescape-access-"):
                pass
            return True
        except (OSError, ValueError):
            return False

    def _save(self):
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root,
                                             prefix=".export-locations-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self._locations, handle, ensure_ascii=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def initial_directory(self, category):
        if category not in {"settings", "profiles"}:
            raise ValueError("Unknown export category.")
        with self._lock:
            remembered = self._locations.get(category)
            if remembered and self._available(Path(remembered)):
                return Path(remembered)
            if remembered is not None:
                # Forget unavailable locations, even if the drive later reappears.
                self._locations.pop(category, None)
                self._save()
            default = self.root / category
            default.mkdir(parents=True, exist_ok=True)
            if not self._available(default):
                raise OSError(f"The default export folder is not writable: {default}")
            return default

    def remember(self, category, folder):
        if category not in {"settings", "profiles"}:
            raise ValueError("Unknown export category.")
        folder = Path(folder).resolve()
        if not self._available(folder):
            return False
        with self._lock:
            self._locations[category] = str(folder)
            return self._save()
