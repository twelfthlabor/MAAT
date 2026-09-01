"""Bounded, cached geocoding for public place names found in source material."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from backend.core.config import settings


@dataclass(slots=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str
    precision: str
    provider: str = "OpenStreetMap Nominatim"


_request_lock = asyncio.Lock()
_last_request_at = 0.0


def _cache_path() -> Path:
    return settings.cache_dir / "geocoding.json"


def _load_cache() -> dict[str, dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _precision(result: dict[str, Any]) -> str:
    address = result.get("address") or {}
    if address.get("house_number") and address.get("road"):
        return "address"
    if address.get("road") or result.get("addresstype") in {"road", "highway"}:
        return "street"
    if any(address.get(key) for key in ("suburb", "neighbourhood", "quarter", "borough")):
        return "neighbourhood"
    if any(address.get(key) for key in ("city", "town", "village", "municipality")):
        return "city"
    return "area"


async def geocode_public_place(location: str, province: str | None = None) -> GeocodeResult | None:
    """Geocode a public place description without sending subject identifiers."""

    global _last_request_at

    if not settings.enable_location_geocoding or not location.strip():
        return None

    query = ", ".join(part for part in (location.strip(), province, "Canada") if part)
    cache_key = " ".join(query.lower().split())
    cache = _load_cache()
    if cache_key in cache:
        try:
            return GeocodeResult(**cache[cache_key])
        except (TypeError, ValueError):
            pass

    async with _request_lock:
        cache = _load_cache()
        if cache_key in cache:
            try:
                return GeocodeResult(**cache[cache_key])
            except (TypeError, ValueError):
                pass

        delay = settings.geocoder_min_interval_seconds - (time.monotonic() - _last_request_at)
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            async with httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                headers={"User-Agent": settings.geocoder_user_agent},
            ) as client:
                response = await client.get(
                    f"{settings.geocoder_url.rstrip('/')}/search",
                    params={
                        "q": query,
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "countrycodes": "ca",
                        "limit": 1,
                    },
                )
            _last_request_at = time.monotonic()
            response.raise_for_status()
            results = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return None

        if not isinstance(results, list) or not results:
            return None

        best = results[0]
        try:
            result = GeocodeResult(
                latitude=float(best["lat"]),
                longitude=float(best["lon"]),
                display_name=str(best.get("display_name") or query),
                precision=_precision(best),
            )
        except (KeyError, TypeError, ValueError):
            return None

        cache[cache_key] = asdict(result)
        try:
            _save_cache(cache)
        except OSError:
            pass
        return result
