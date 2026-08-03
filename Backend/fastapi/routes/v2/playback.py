from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from Backend.fastapi.mappers.v2_mappers import (
    build_manifest_from_episode,
    build_manifest_from_movie_doc,
)
from Backend.fastapi.routes.v2.media import find_one_media
from Backend.fastapi.schemas.v2_schemas import (
    ApiError,
    ApiSuccess,
    ProgressReportRequest,
    fail,
    ok,
)
from Backend.fastapi.security.v2_jwt import CurrentUser, require_user
from Backend.fastapi.v2_utils import tracking_db

router = APIRouter(tags=["V2 — Playback (Sources / Subtitles / Progress)"])


async def _load_subtitles_for(tmdb_id: int, season: Optional[int], episode: Optional[int]) -> List[Dict[str, Any]]:
    tdb = tracking_db()
    if tdb is None:
        return []
    query: Dict[str, Any] = {"tmdb_id": int(tmdb_id)}
    if season is not None:
        query["season"] = int(season)
    if episode is not None:
        query["episode"] = int(episode)
    try:
        return [s async for s in tdb["subtitles"].find(query).limit(100)]
    except Exception:
        return []


def _check_subscription_ok(doc: Dict[str, Any]) -> Optional[tuple[ApiError, int]]:
    status_val = str(doc.get("subscription_status") or "inactive").lower()
    expires = doc.get("subscription_expiry")
    if expires:
        try:
            if expires < datetime.now(timezone.utc):
                return fail(
                    "SUBSCRIPTION_EXPIRED",
                    "Subscription expired. Renew to continue streaming.",
                    status=402,
                    details={
                        "expires_at": expires.isoformat()
                        if hasattr(expires, "isoformat")
                        else str(expires)
                    },
                )
        except Exception:
            pass
    if status_val in ("expired", "inactive") and expires is None:
        return fail("SUBSCRIPTION_REQUIRED", "Active subscription required.", status=402)
    return None


@router.get("/playback/movie/{tmdb_id}/{db_index}")
async def playback_movie(
    tmdb_id: int,
    db_index: int,
    current: CurrentUser = Depends(require_user),
):
    sub_err = _check_subscription_ok(current.user_doc)
    if sub_err is not None:
        return sub_err

    doc = await find_one_media("movie", tmdb_id=tmdb_id, prefer_db_index=db_index)
    if not doc:
        return fail("NOT_FOUND", "Movie not available.", status=404)
    if not doc.get("telegram"):
        return fail(
            "NO_STREAMS",
            "No streaming sources available for this title yet.",
            status=404,
            details={"tmdb_id": tmdb_id},
        )
    subs = await _load_subtitles_for(tmdb_id, None, None)
    manifest = build_manifest_from_movie_doc(doc, subtitles=subs)
    return ok(manifest)


@router.get("/playback/series/{tmdb_id}/{db_index}/season/{s}/episode/{e}")
async def playback_series_episode(
    tmdb_id: int,
    db_index: int,
    s: int,
    e: int,
    current: CurrentUser = Depends(require_user),
):
    sub_err = _check_subscription_ok(current.user_doc)
    if sub_err is not None:
        return sub_err

    doc = await find_one_media("series", tmdb_id=tmdb_id, prefer_db_index=db_index)
    if not doc:
        return fail("NOT_FOUND", "Show not available.", status=404)
    seasons = doc.get("seasons") or []
    target_season = None
    for s_doc in seasons:
        if int(s_doc.get("season_number") or s_doc.get("season") or -1) == int(s):
            target_season = s_doc
            break
    if not target_season:
        return fail("SEASON_NOT_FOUND", f"Season {s} not available.", status=404)
    episodes = target_season.get("episodes") or []
    found_ep = None
    for e_doc in episodes:
        if int(e_doc.get("episode_number") or e_doc.get("episode") or -1) == int(e):
            found_ep = e_doc
            break
    if not found_ep:
        return fail("EPISODE_NOT_FOUND", f"Episode {e} not available.", status=404)
    if not (found_ep.get("telegram") or []):
        return fail(
            "NO_STREAMS",
            "No sources for this episode yet.",
            status=404,
            details={"tmdb_id": tmdb_id, "s": s, "e": e},
        )
    subs = await _load_subtitles_for(tmdb_id, s, e)
    manifest = build_manifest_from_episode(
        doc, season_number=int(s), episode_number=int(e), subtitles=subs
    )
    return ok(manifest)


@router.post("/playback/progress")
async def playback_progress(
    body: ProgressReportRequest,
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        return ok({"saved": False})
    key = f"{body.media_type}:{body.tmdb_id}:{body.db_index}"
    if body.season is not None and body.episode is not None:
        key += f":{body.season}:{body.episode}"
    now = datetime.now(timezone.utc)
    total = body.total_time or 1
    try:
        pct = float(body.current_time) / max(1.0, float(total))
    except Exception:
        pct = 0.0
    completed = pct >= 0.90
    try:
        await tdb["subscribers"].update_one(
            {"_id": current.user_id},
            {
                "$set": {
                    f"watch_progress.{key}": {
                        "current_time": float(body.current_time),
                        "total_time": total,
                        "pct": round(pct, 4),
                        "completed": completed,
                        "updated_at": now,
                    },
                    "updated_at": now,
                }
            },
            upsert=False,
        )
    except Exception:
        pass
    return ok({"saved": True})
