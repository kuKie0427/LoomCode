"""Models.dev API client — fetches model metadata (context windows, pricing)
from opencode's public model catalog.

Provides a module-level singleton that is populated lazily on first access
and refreshed in a background thread every 60 minutes.

Usage:
    from loom.agent.models_dev import lookup_context_window
    ctx = lookup_context_window("anthropic", "claude-sonnet-4-5")  # -> 200000
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

CACHE_DIR = Path.home() / ".loom"
CACHE_FILE = CACHE_DIR / "models_dev.json"
FETCH_URL = "https://models.dev/api.json"
USER_AGENT = "loom/0.1 (coding-agent; loom)"
FRESH_TTL = 300  # 5 minutes — fresh enough for a cache validity check
REFRESH_INTERVAL = 3600  # 60 minutes — background refresh

_lock = threading.Lock()
_data: dict[str, dict] | None = None
_started: bool = False


def _fetch() -> dict[str, dict] | None:
    """Fetch models.dev catalog. Returns None on any failure (network, parse, …)."""
    req = urllib.request.Request(FETCH_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _load_cache() -> dict[str, dict] | None:
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_cache(data: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.rename(CACHE_FILE)
    except Exception:
        pass


def _refresh() -> None:
    """Fetch fresh data from models.dev and update the in-memory cache + disk cache."""
    global _data
    fetched = _fetch()
    if fetched is not None:
        _save_cache(fetched)
        with _lock:
            _data = fetched


def _background_refresh_loop() -> None:
    """Periodically refresh the cache in a daemon thread."""
    while True:
        time.sleep(REFRESH_INTERVAL)
        _refresh()


def _ensure_populated() -> None:
    """Ensure the cache is populated. Called on first ``lookup_context_window``.

    Loads from disk cache immediately (fast path), then kicks off a
    background fetch so the next call gets fresher data.  Also starts
    the periodic refresh thread on first call (idempotent).
    """
    global _data, _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _started = True

    # Load disk cache immediately so the first lookup doesn't block.
    cached = _load_cache()
    if cached is not None:
        with _lock:
            _data = cached

    # No cache at all — do a synchronous fetch so callers get data.
    if _data is None:
        _refresh()

    # Background fetch to freshen the cache (runs after sync fetch so it
    # won't re-download on the same process start).
    threading.Thread(target=_refresh, daemon=True).start()

    # Periodic refresh thread.
    threading.Thread(target=_background_refresh_loop, daemon=True).start()

    # Register models.dev providers into the global PROVIDERS registry.
    # Runs after the cache is populated (either from disk or from the
    # sync fetch above) so that dynamic OpenAI-compatible providers are
    # available immediately.
    _register_providers()


def _register_providers() -> None:
    """Register OpenAI-compatible models.dev providers into PROVIDERS."""
    from loom.agent.providers.openai_compatible import register_models_dev_providers

    register_models_dev_providers()


def _get_provider(provider_id: str) -> dict | None:
    """Return the provider entry from the models.dev cache, or None."""
    _ensure_populated()
    with _lock:
        if _data is None:
            return None
        return _data.get(provider_id)


def _get_provider_data() -> dict[str, dict] | None:
    """Return the full models.dev catalog (all providers), or None if not ready."""
    _ensure_populated()
    with _lock:
        return _data


def lookup_context_window(provider_id: str, model_id: str) -> int | None:
    """Look up a model's context window from the models.dev cache.

    Returns ``None`` when the provider or model is not found in the
    catalog — callers should fall back to their hardcoded lookup /
    default window.
    """
    provider = _get_provider(provider_id)
    if provider is None:
        return None
    models = provider.get("models", {})
    model = models.get(model_id)
    if model is None:
        return None
    return model.get("limit", {}).get("context")


def list_models(provider_id: str) -> list[str] | None:
    """Return all model IDs for a provider from the models.dev cache.

    Returns ``None`` when the provider is not found in the catalog.
    """
    provider = _get_provider(provider_id)
    if provider is None:
        return None
    return list(provider.get("models", {}).keys())


def list_models_sorted(provider_id: str) -> list[tuple[str, str]] | None:
    """Return ``(model_id, display_name)`` pairs sorted by family then recency.

    Deprecated models are excluded.  Models with unknown family are grouped at
    the end.  Within each family the newest release appears first.
    Returns ``None`` when the provider is not found in the catalog.
    """
    provider = _get_provider(provider_id)
    if provider is None:
        return None

    raw: list[tuple[str, str, str, str]] = []  # (family, release_date, id, name)
    for mid, info in provider.get("models", {}).items():
        if info.get("status") == "deprecated":
            continue
        family = info.get("family") or "~zzz"  # unknown families sink to bottom
        name = info.get("name") or mid
        release = info.get("release_date") or "0000-00-00"
        raw.append((family, release, mid, name))

    # Sort by family asc, release_date desc, then model_id asc.
    raw.sort(key=lambda x: (x[0], _negate_date(x[1]), x[2]))
    return [(mid, name) for _, _, mid, name in raw]


def _negate_date(d: str) -> str:
    """Turn a date string so that sorting asc gives newest first."""
    return d if d.startswith("0000") else f"{9999 - int(d[:4]):04d}{d[4:]}"
