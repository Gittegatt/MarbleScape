"""One catalogue interface for MarbleScape's public still-image providers."""

from __future__ import annotations

import copy
import inspect
import hashlib
import json
import os
from pathlib import Path
import threading
from concurrent.futures import Future

from marblescape_himawari import HimawariClient
from marblescape_noaa import NOAAClient
from marblescape_slider import SliderClient
from marblescape_worldview import WorldviewClient
from marblescape_eumetsat import EumetsatCatalogueClient


NOAA_PROVIDERS = frozenset(("goes_east", "goes_west", "solar"))
CATALOGUE_PROVIDERS = (*sorted(NOAA_PROVIDERS), "himawari", "slider", "worldview")
_CACHE_VERSION = 1


class _CatalogueDiskCache:
    """Small, atomic JSON cache containing only provider catalogue metadata."""

    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        self._lock = threading.RLock()
        self._data = {
            "version": _CACHE_VERSION, "providers": {}, "eumetsat": [],
            "copernicus": {},
        }
        self._load()

    def _load(self):
        if self.path is None or not self.path.is_file():
            return
        try:
            if self.path.stat().st_size > 32 * 1024 * 1024:
                return
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and value.get("version") == _CACHE_VERSION
                    and isinstance(value.get("providers"), dict)
                    and isinstance(value.get("eumetsat"), list)
                    and isinstance(value.get("copernicus", {}), dict)):
                self._data = value
                self._data.setdefault("copernicus", {})
        except (OSError, ValueError, TypeError):
            # A damaged optional cache must never prevent the application start.
            return

    def _save(self):
        if self.path is None:
            return
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def areas(self, provider):
        with self._lock:
            value = self._data["providers"].get(provider, {}).get("areas")
            return copy.deepcopy(value) if isinstance(value, list) else None

    def products(self, provider, area_id):
        with self._lock:
            value = self._data["providers"].get(provider, {}).get("products", {}).get(str(area_id))
            return copy.deepcopy(value) if isinstance(value, list) else None

    def store_areas(self, provider, items):
        if not isinstance(items, list):
            return
        with self._lock:
            entry = self._data["providers"].setdefault(provider, {"areas": [], "products": {}})
            entry["areas"] = copy.deepcopy(items)
            entry.setdefault("products", {})
            self._save()

    def store_products(self, provider, area_id, items):
        if not isinstance(items, list):
            return
        with self._lock:
            entry = self._data["providers"].setdefault(provider, {"areas": [], "products": {}})
            entry.setdefault("areas", [])
            entry.setdefault("products", {})[str(area_id)] = copy.deepcopy(items)
            self._save()

    def store_provider(self, provider, areas, products):
        if not isinstance(areas, list) or not isinstance(products, dict):
            return
        with self._lock:
            self._data["providers"][provider] = {
                "areas": copy.deepcopy(areas),
                "products": copy.deepcopy(products),
            }
            self._save()

    def eumetsat(self):
        with self._lock:
            value = self._data.get("eumetsat")
            return copy.deepcopy(value) if isinstance(value, list) and value else None

    def store_eumetsat(self, items):
        if not isinstance(items, list) or not items:
            return
        with self._lock:
            self._data["eumetsat"] = copy.deepcopy(items)
            self._save()

    @staticmethod
    def _copernicus_key(profile, output_size):
        fields = (
            "configuration", "mission", "product", "layer", "latitude",
            "longitude", "map_zoom", "max_cloud_cover",
        )
        value = {key: profile.get(key) for key in fields}
        value["output_size"] = [int(output_size[0]), int(output_size[1])]
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def copernicus_dates(self, profile, output_size):
        key = self._copernicus_key(profile, output_size)
        with self._lock:
            value = self._data["copernicus"].get(key, {}).get("dates")
            return copy.deepcopy(value) if isinstance(value, list) else None

    def store_copernicus_dates(self, profile, output_size, dates):
        if not isinstance(dates, list):
            return
        key = self._copernicus_key(profile, output_size)
        with self._lock:
            self._data["copernicus"][key] = {"dates": copy.deepcopy(dates)}
            self._save()

    def summary(self, providers):
        with self._lock:
            areas = products = resolutions = 0
            available = 0
            for provider in providers:
                entry = self._data["providers"].get(provider, {})
                cached_areas = entry.get("areas", [])
                cached_products = entry.get("products", {})
                if cached_areas or cached_products:
                    available += 1
                areas += len(cached_areas) if isinstance(cached_areas, list) else 0
                for values in cached_products.values() if isinstance(cached_products, dict) else ():
                    if isinstance(values, list):
                        products += len(values)
                        resolutions += sum(len(item.get("resolutions", ())) for item in values if isinstance(item, dict))
            return available, areas, products, resolutions


class CatalogueClient:
    """Route provider queries and coordinate a single global metadata refresh."""

    def __init__(self, timeout=90, user_agent="MarbleScape", noaa=None, himawari=None,
                 slider=None, worldview=None, eumetsat=None, cache_path=None, retries=2):
        self._cache = _CatalogueDiskCache(cache_path)
        self.retries = max(1, min(9, int(retries)))
        self._fallback_warnings = {}
        self.noaa = noaa if noaa is not None else NOAAClient(timeout=timeout, user_agent=user_agent)
        self.himawari = himawari if himawari is not None else HimawariClient(
            timeout=timeout, user_agent=user_agent
        )
        self.slider = slider if slider is not None else SliderClient(
            timeout=timeout, user_agent=user_agent
        )
        self.worldview = worldview if worldview is not None else WorldviewClient(
            timeout=timeout, user_agent=user_agent
        )
        self.eumetsat = eumetsat if eumetsat is not None else EumetsatCatalogueClient(
            timeout=timeout, user_agent=user_agent,
            cached_catalogue=self._cache.eumetsat(),
            on_catalogue=self._cache.store_eumetsat,
            retries=self.retries,
        )
        self._lock = threading.RLock()
        self._future = None
        self._status = {"running": False, "done": 0, "total": 0,
                        "message": "", "error": ""}

    def _client(self, provider):
        if provider in NOAA_PROVIDERS:
            return self.noaa
        if provider == "himawari":
            return self.himawari
        if provider == "slider":
            return self.slider
        if provider == "worldview":
            return self.worldview
        raise ValueError("Unknown catalogue provider: " + str(provider))

    def list_areas(self, provider, refresh=False):
        client = self._client(provider)
        last_value = None
        last_error = None
        for _attempt in range(self.retries + 1 if refresh else 1):
            try:
                last_value = client.list_areas(provider, refresh=refresh)
                warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                if last_value and not warning:
                    self._fallback_warnings.pop(provider, None)
                    self._cache.store_areas(provider, last_value)
                    return last_value
                last_error = warning or "the provider returned no catalogue entries"
            except Exception as exc:
                last_error = str(exc)
        cached = self._cache.areas(provider)
        if cached:
            self._fallback_warnings[provider] = (
                f"{last_error or 'Catalogue update failed'}; using cached catalogue data."
            )
            return cached
        if last_value:
            return last_value
        raise RuntimeError(last_error or "Catalogue data is unavailable.")

    def list_products(self, provider, area_id, refresh=False):
        client = self._client(provider)
        last_value = None
        last_error = None
        for _attempt in range(self.retries + 1 if refresh else 1):
            try:
                last_value = client.list_products(provider, area_id, refresh=refresh)
                warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                if last_value and not warning:
                    self._fallback_warnings.pop(provider, None)
                    self._cache.store_products(provider, area_id, last_value)
                    return last_value
                last_error = warning or "the provider returned no catalogue entries"
            except Exception as exc:
                last_error = str(exc)
        cached = self._cache.products(provider, area_id)
        if cached:
            self._fallback_warnings[provider] = (
                f"{last_error or 'Catalogue update failed'}; using cached catalogue data."
            )
            return cached
        if last_value:
            return last_value
        raise RuntimeError(last_error or "Catalogue data is unavailable.")

    @property
    def catalogue_warning(self):
        warnings = []
        for provider in CATALOGUE_PROVIDERS:
            value = self.catalogue_warning_for(provider)
            if value and value not in warnings:
                warnings.append(value)
        return " | ".join(warnings)

    def catalogue_warning_for(self, provider):
        """Return only the warning belonging to the selected image source."""
        values = (
            self._fallback_warnings.get(provider, ""),
            str(getattr(self._client(provider), "catalogue_warning", "") or "").strip(),
        )
        return " ".join(value for index, value in enumerate(values)
                        if value and value not in values[:index])

    def cached_copernicus_dates(self, profile, output_size):
        return self._cache.copernicus_dates(profile, output_size)

    def store_copernicus_dates(self, profile, output_size, dates):
        self._cache.store_copernicus_dates(profile, output_size, dates)

    def _cache_provider(self, provider):
        client = self._client(provider)
        areas = client.list_areas(provider, refresh=False)
        if not areas:
            return
        products_by_area = {}
        for area in areas:
            area_id = area.get("id") if isinstance(area, dict) else None
            if area_id:
                products = client.list_products(provider, area_id, refresh=False)
                if products:
                    products_by_area[str(area_id)] = products
        self._cache.store_provider(provider, areas, products_by_area)

    def _cached_source_summary(self, label):
        if label == "EUMETSAT":
            items = self._cache.eumetsat() or []
            return {
                "providers": 1 if items else 0,
                "areas": len({item.get("satellite") for item in items if isinstance(item, dict)}),
                "products": len(items), "resolution_options": 0,
            }
        providers = tuple(NOAA_PROVIDERS) if label == "NOAA" else {
            "Himawari": ("himawari",), "CIRA SLIDER": ("slider",),
            "NASA Worldview": ("worldview",),
        }[label]
        available, areas, products, resolutions = self._cache.summary(providers)
        return {"providers": available, "areas": areas, "products": products,
                "resolution_options": resolutions}

    @property
    def catalogue_refresh_status(self):
        with self._lock:
            return dict(self._status)

    def _progress(self, done, total, message, callback):
        with self._lock:
            self._status.update(done=done, total=total, message=message)
        if callback:
            try:
                callback(done, total, message)
            except Exception:
                pass

    def refresh_all_catalogues(self, refresh=True, progress=None):
        with self._lock:
            future = self._future
            owner = future is None
            if owner:
                future = self._future = Future()
                self._status = {"running": True, "done": 0, "total": 5,
                                "message": "Loading NOAA catalogues...", "error": ""}
            status = dict(self._status)
        self._progress(status["done"], status["total"], status["message"], progress)
        if not owner:
            return copy.deepcopy(future.result())
        summaries = []
        errors = []
        notices = []
        try:
            sources = (
                    ("NOAA", self.noaa), ("Himawari", self.himawari),
                    ("CIRA SLIDER", self.slider),
                    ("NASA Worldview", self.worldview),
                    ("EUMETSAT", self.eumetsat),
            )
            for index, (label, client) in enumerate(sources, 1):
                self._progress(index - 1, len(sources), f"Loading {label} catalogues...", progress)

                def source_progress(done, total, detail, source_index=index, source_label=label):
                    total = max(0, int(total))
                    done = max(0, int(done))
                    fraction = min(1.0, done / total) if total else 0.0
                    step = f" ({min(done, total)}/{total})" if total else ""
                    self._progress(source_index - 1 + fraction, len(sources),
                                   f"{source_label}: {detail}{step}", progress)

                summary = None
                last_problem = ""
                attempt_count = (
                    1 if label == "EUMETSAT" and hasattr(client, "retries")
                    else self.retries + 1 if refresh else 1
                )
                for _attempt in range(attempt_count):
                    try:
                        if label == "EUMETSAT":
                            items = client.catalogue(refresh=refresh)
                            warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                            summary = {
                                "providers": 1,
                                "areas": len({item["satellite"] for item in items}),
                                "products": len(items),
                                "resolution_options": 0,
                                "errors": [], "warning": warning,
                                "complete": not warning,
                            }
                        else:
                            method = client.refresh_all_catalogues
                            if "progress" in inspect.signature(method).parameters:
                                summary = method(refresh=refresh, progress=source_progress)
                            else:
                                summary = method(refresh=refresh)
                        if summary.get("complete"):
                            break
                        last_problem = str(summary.get("warning") or "catalogue update was incomplete")
                    except Exception as exc:
                        last_problem = str(exc)
                        summary = None
                if summary is None or not summary.get("complete"):
                    cached = self._cached_source_summary(label)
                    cached_available = bool(cached["providers"] or cached["products"])
                    detail = f"{label}: {last_problem or 'catalogue update failed'}"
                    if cached_available:
                        detail += "; using cached catalogue data."
                        cached.update(errors=[detail], warning=detail, complete=False)
                        summary = cached
                    elif summary is None:
                        summary = {**cached, "errors": [detail],
                                   "warning": detail, "complete": False}
                else:
                    try:
                        if label == "EUMETSAT":
                            self._cache.store_eumetsat(items)
                        else:
                            providers = tuple(NOAA_PROVIDERS) if label == "NOAA" else {
                                "Himawari": ("himawari",), "CIRA SLIDER": ("slider",),
                                "NASA Worldview": ("worldview",),
                            }[label]
                            for provider in providers:
                                self._cache_provider(provider)
                    except Exception:
                        pass
                summaries.append(summary)
                source_errors = [str(value) for value in summary.get("errors", ())]
                errors.extend(source_errors)
                notice = str(summary.get("warning", "") or "").strip()
                if (notice and notice not in notices
                        and not any(error and error in notice for error in source_errors)):
                    notices.append(notice)
                self._progress(index, len(sources), f"{label} catalogues loaded.", progress)
            warning = ""
            details = errors + [value for value in notices if value not in errors]
            if details:
                warning = "Catalogue refresh is incomplete. " + " | ".join(details[:6])
                if len(details) > 6:
                    warning += " | +%s further errors" % (len(details) - 6)
            result = {
                key: sum(int(value.get(key, 0)) for value in summaries)
                for key in ("providers", "areas", "products", "resolution_options")
            }
            result.update(errors=errors, warning=warning,
                          complete=not details and all(value.get("complete") for value in summaries))
            message = "Catalogue refresh completed." if result["complete"] else "Catalogue refresh completed with unavailable entries."
            with self._lock:
                self._status.update(running=False, done=len(sources), total=len(sources),
                                    message=message, error=warning)
            future.set_result(result)
            return copy.deepcopy(result)
        except BaseException as exc:
            with self._lock:
                self._status.update(running=False, message="Catalogue refresh failed.", error=str(exc))
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._future is future:
                    self._future = None
