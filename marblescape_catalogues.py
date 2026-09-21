"""One catalogue interface for MarbleScape's public still-image providers."""

from __future__ import annotations

import copy
import threading
from concurrent.futures import Future

from marblescape_himawari import HimawariClient
from marblescape_noaa import NOAAClient
from marblescape_slider import SliderClient
from marblescape_worldview import WorldviewClient
from marblescape_eumetsat import EumetsatCatalogueClient


NOAA_PROVIDERS = frozenset(("goes_east", "goes_west", "solar"))


class CatalogueClient:
    """Route provider queries and coordinate a single global metadata refresh."""

    def __init__(self, timeout=90, user_agent="MarbleScape", noaa=None, himawari=None,
                 slider=None, worldview=None, eumetsat=None):
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
            timeout=timeout, user_agent=user_agent
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
        return self._client(provider).list_areas(provider, refresh=refresh)

    def list_products(self, provider, area_id, refresh=False):
        return self._client(provider).list_products(provider, area_id, refresh=refresh)

    @property
    def catalogue_warning(self):
        warnings = [str(getattr(client, "catalogue_warning", "") or "").strip()
                    for client in (self.noaa, self.himawari, self.slider, self.worldview)]
        return " | ".join(value for value in warnings if value)

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
                    ("EUMETSAT EUMETView", self.eumetsat),
            )
            for index, (label, client) in enumerate(sources, 1):
                self._progress(index - 1, len(sources), f"Loading {label} catalogues...", progress)
                try:
                    if label == "EUMETSAT EUMETView":
                        items = client.catalogue(refresh=refresh)
                        summary = {
                            "providers": 1,
                            "areas": len({item["satellite"] for item in items}),
                            "products": len(items),
                            "resolution_options": 0,
                            "errors": [], "warning": "", "complete": True,
                        }
                    else:
                        summary = client.refresh_all_catalogues(refresh=refresh)
                except Exception as exc:
                    summary = {"providers": 0, "areas": 0, "products": 0,
                               "resolution_options": 0, "errors": [f"{label}: {exc}"],
                               "warning": str(exc), "complete": False}
                summaries.append(summary)
                errors.extend(str(value) for value in summary.get("errors", ()))
                notice = str(summary.get("warning", "") or "").strip()
                if notice and notice not in notices:
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
