from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, Query

from Backend.fastapi.mappers.v2_mappers import to_media_detail, to_media_summary
from Backend.fastapi.schemas.v2_schemas import (
    ApiError,
    ApiSuccess,
    fail,
    ok,
)
from Backend.fastapi.security.v2_jwt import CurrentUser, optional_user
from Backend.fastapi.v2_utils import (
    find_one_media,
    iterate_media,
    tracking_db,
)

router = APIRouter(tags=["V2 — Media Detail"])


@router.get("/media/{media_type}/{tmdb_id}/{db_index}")
async def media_detail(
    media_type: str,
    tmdb_id: int,
    db_index: int,
    current: CurrentUser = Depends(optional_user),
):
    mt = "series" if media_type in ("tv", "series") else "movie"
    doc = await find_one_media(mt, tmdb_id=tmdb_id, prefer_db_index=db_index)
    if not doc:
        return fail("NOT_FOUND", "Media not found in catalog.", status=404)
    langs: List[str] = []
    tdb = tracking_db()
    if tdb is not None:
        try:
            async for s in tdb["subtitles"].find({"tmdb_id": int(tmdb_id)}).limit(50):
                lg = str(s.get("lang_code") or s.get("language") or "").strip()
                if lg and lg not in langs:
                    langs.append(lg)
        except Exception:
            pass
    return ok(to_media_detail(doc, media_type_override=mt, user_state=None, available_subtitle_langs=langs))


@router.get("/media/{media_type}/{tmdb_id}/{db_index}/related")
async def media_related(
    media_type: str,
    tmdb_id: int,
    db_index: int,
    limit: int = Query(12, ge=1, le=48),
    current: CurrentUser = Depends(optional_user),
):
    mt = "series" if media_type in ("tv", "series") else "movie"
    sort = [("rating", -1)]
    docs = await iterate_media(mt, sort=sort, limit=limit + 1)
    out: List[Any] = []
    for d in docs:
        if int(d.get("tmdb_id") or 0) == int(tmdb_id):
            continue
        d.setdefault("db_index", db_index or 1)
        out.append(to_media_summary(d, media_type_override=mt))
        if len(out) >= limit:
            break
    return ok({"items": out})
