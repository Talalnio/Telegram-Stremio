"""Shared v2 utilities (async DB access, lookup helpers, etc).

Provides small thin wrappers over Backend.db (async motor) so v2 routers
follow the same access pattern as existing api_routes.py (storage_# collections
for movies/shows, tracking db for subscribers/subtitles/settings).
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from Backend import db as _app_db

# ---------------------------------------------------------------------------
# DB handle shortcuts
# ---------------------------------------------------------------------------


def tracking_db():
    """The tracking database holds subscribers, subtitles, settings, state."""
    return _app_db.dbs.get("tracking")


def storage_dbs() -> List[Tuple[str, Any]]:
    """Return list of (db_key, AsyncIOMotorDatabase) for every active storage shard."""
    return [(k, v) for k, v in _app_db.dbs.items() if str(k).startswith("storage_")]


def active_storage_db():
    """Return currently active storage database (based on current_db_index state)."""
    key = f"storage_{int(getattr(_app_db, 'current_db_index', 1) or 1)}"
    dbs = dict(storage_dbs())
    if key in dbs:
        return dbs[key]
    if dbs:
        return next(iter(dbs.values()))
    return None


# ---------------------------------------------------------------------------
# Helpers: assign a stable numeric db_index to each active storage shard,
# regardless of how the underlying DB key is named (storage_1, storage_main, etc.)
# ---------------------------------------------------------------------------

_cached_db_index: Dict[str, int] = {}


def _refresh_db_index_once() -> Dict[str, int]:
    """Assign a stable 1-based numeric db_index to every storage_* shard key.

    The mapping is deterministic: keys are sorted alphabetically and the first
    active shard always becomes db_index=1. This guarantees that `iterate_media`
    and `find_one_media` always agree on the same composite ID, regardless of
    whether the shard is named ``storage_1``, ``storage_main`` or any other
    non-numeric suffix.
    """
    global _cached_db_index
    keys = sorted(str(k) for k, _ in storage_dbs())
    _cached_db_index = {k: i + 1 for i, k in enumerate(keys)}
    return _cached_db_index


def _resolve_db_index(db_key: str) -> int:
    """Return the stable numeric db_index for a given shard key (>= 1)."""
    mapping = _cached_db_index if _cached_db_index else _refresh_db_index_once()
    if db_key in mapping:
        return mapping[db_key]
    # Unknown key: rebuild the mapping once to tolerate runtime shard additions.
    mapping = _refresh_db_index_once()
    return mapping.get(db_key, 1)


def media_collection_name(media_type: str) -> str:
    return "tv" if str(media_type).lower() in ("tv", "series", "show", "shows") else "movie"


# ---------------------------------------------------------------------------
# Aggregate helpers: iterate multi-shard storage for movies/shows
# ---------------------------------------------------------------------------


async def iterate_media(
    media_type: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    sort: Optional[List[Tuple[str, int]]] = None,
    limit: Optional[int] = None,
    skip: int = 0,
) -> List[Dict[str, Any]]:
    """Scan all storage DBs for docs of the given media_type (movie/tv), merge + sort + paginate."""
    q = query or {}
    results: List[Dict[str, Any]] = []
    coll = media_collection_name(media_type)
    _ = _refresh_db_index_once()  # ensure stable numeric db_index map is fresh
    for _db_key, storage in storage_dbs():
        idx = _resolve_db_index(str(_db_key))
        cursor = storage[coll].find(q)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit + 1)
        async for doc in cursor:
            doc["db_index"] = idx  # force stable index (override any stale value)
            results.append(doc)
            if limit and len(results) >= limit:
                return results
    if sort:
        # In-memory sort to merge multiple shards coherently
        def _key(d: Dict[str, Any]) -> tuple:
            out: list = []
            for field, direction in sort:
                val = d.get(field)
                out.append(-1 if direction == -1 else 1)
                out.append(0 if val is None else val)
            return tuple(out)
        results.sort(key=_key)
    return results


async def count_media(media_type: str, query: Optional[Dict[str, Any]] = None) -> int:
    q = query or {}
    coll = media_collection_name(media_type)
    total = 0
    for _k, storage in storage_dbs():
        try:
            total += int(await storage[coll].count_documents(q))
        except Exception:
            try:
                total += int(await storage[coll].estimated_document_count())
            except Exception:
                total += 0
    return total


async def find_one_media(
    media_type: str,
    *,
    tmdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    prefer_db_index: Optional[int] = None,
    _log: bool = True,
) -> Optional[Dict[str, Any]]:
    q: Dict[str, Any] = {}
    if tmdb_id is not None:
        q["tmdb_id"] = int(tmdb_id)
    if imdb_id:
        q["imdb_id"] = str(imdb_id)
    if not q:
        return None
    coll = media_collection_name(media_type)
    # --- Sort shards: try the user-supplied numeric db_index first, then the rest ---
    dbs_raw = list(storage_dbs())
    stable = _refresh_db_index_once()
    # Build reverse map: numeric db_index -> shard key(s)
    index_to_keys: Dict[int, List[str]] = {}
    for k, _v in dbs_raw:
        index_to_keys.setdefault(_resolve_db_index(str(k)), []).append(str(k))
    preferred_keys = set(index_to_keys.get(int(prefer_db_index), [])) if prefer_db_index is not None else set()
    try:
        ordered = sorted(
            dbs_raw,
            key=lambda kv: (
                0 if str(kv[0]) in preferred_keys else 1,
                str(kv[0]),
            ),
        )
    except Exception:
        ordered = dbs_raw
    searched: List[str] = []
    for _k, storage in ordered:
        searched.append(f"{_k}(db{_resolve_db_index(str(_k))})")
        doc = await storage[coll].find_one(q)
        if doc:
            doc["db_index"] = _resolve_db_index(str(_k))
            if _log:
                import logging
                logger = logging.getLogger("v2.find_one")
                logger.info(
                    "[find_one_media] HIT media_type=%s query=%s shard=%s (prefer_db_index=%s, searched=%s)",
                    media_type, q, _k, prefer_db_index, " -> ".join(searched),
                )
            return doc
    if _log:
        import logging
        logger = logging.getLogger("v2.find_one")
        logger.warning(
            "[find_one_media] MISS media_type=%s query=%s prefer_db_index=%s searched_shards=[%s] available_keys=%s",
            media_type, q, prefer_db_index, ", ".join(searched), dict(stable),
        )
    return None
