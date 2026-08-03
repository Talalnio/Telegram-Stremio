from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from Backend.fastapi.mappers.v2_mappers import (
    to_catalog_response,
    to_hero_item,
    to_media_summary,
)
from Backend.fastapi.schemas.v2_schemas import (
    ApiSuccess,
    CustomCatalogPreview,
    DiscoverHome,
    MediaSummary,
    ok,
)
from Backend.fastapi.security.v2_jwt import CurrentUser, optional_user
from Backend.fastapi.v2_utils import (
    count_media,
    iterate_media,
    tracking_db,
)

router = APIRouter(tags=["V2 — Catalog & Discovery"])

_SORT_MAP = {
    "rating": [("rating", -1), ("release_year", -1)],
    "newest": [("release_year", -1), ("updated_on", -1)],
    "oldest": [("release_year", 1)],
    "alphabetical": [("title", 1)],
    "popular": [("rating", -1), ("updated_on", -1)],
}


@router.get("/discover/home")
async def discover_home(current: CurrentUser = Depends(optional_user)) -> ApiSuccess[DiscoverHome]:
    movies_best = await iterate_media("movie", sort=_SORT_MAP["popular"], limit=12)
    shows_best = await iterate_media("series", sort=_SORT_MAP["popular"], limit=8)
    hero_docs = (movies_best[:4] + shows_best[:2])[:6]

    hero = [
        to_hero_item(d, media_type_override=("series" if d.get("seasons") else "movie"))
        for d in hero_docs
    ]
    trending: List[MediaSummary] = [
        to_media_summary(d, media_type_override=("series" if d.get("seasons") else "movie"))
        for d in (movies_best + shows_best)[:12]
    ]
    latest_movies = [
        to_media_summary(d, media_type_override="movie")
        for d in await iterate_media("movie", sort=_SORT_MAP["newest"], limit=12)
    ]
    latest_shows = [
        to_media_summary(d, media_type_override="series")
        for d in await iterate_media("series", sort=_SORT_MAP["newest"], limit=12)
    ]

    custom_previews: List[CustomCatalogPreview] = []
    tdb = tracking_db()
    if tdb is not None:
        try:
            cc_col = tdb.get_collection("custom_catalogs")
            if cc_col is not None:
                async for cc in cc_col.find({}).limit(6):
                    items = list((cc.get("items") or [])[:12])
                    custom_previews.append(
                        CustomCatalogPreview(
                            id=str(cc.get("_id") or cc.get("id") or f"cc_{id(cc)}"),
                            name=str(cc.get("name") or "Custom Collection"),
                            poster=str(cc.get("poster") or "") or None,
                            items_count=int(cc.get("items_count") or len(items)),
                            items=[],
                        )
                    )
        except Exception:
            pass

    return ok(DiscoverHome(
        hero_carousel=hero,
        trending_now=trending,
        top_rated=list(trending),
        latest_movies=latest_movies,
        latest_shows=latest_shows,
        continue_watching=[],
        my_list=[],
        custom_catalogs=custom_previews,
    ))


@router.get("/catalog/{media_type}")
async def catalog(
    media_type: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100000),
    sort: str = Query("popular"),
    search: Optional[str] = Query(None),
):
    mt = "series" if media_type in ("tv", "series", "shows", "show") else "movie"
    sort_spec = _SORT_MAP.get(sort, _SORT_MAP["popular"])
    query: Dict[str, Any] = {}
    if search:
        ql = search.lower()
        docs = await iterate_media(mt, sort=sort_spec, limit=page_size * 200)
        docs = [d for d in docs if ql in str(d.get("title") or "").lower()]
        total_items = len(docs)
    else:
        total_items = await count_media(mt, query)
        docs = await iterate_media(
            mt,
            query=query,
            sort=sort_spec,
            limit=page_size,
            skip=(page - 1) * page_size,
        )
    total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1
    skip = (page - 1) * page_size
    page_docs = docs if search else docs[:page_size]
    if search:
        page_docs = docs[skip : skip + page_size]

    res = to_catalog_response(
        page_docs,
        media_type_override=mt,
        current_page=page,
        total_pages=total_pages,
        total_items=total_items,
        page_size=page_size,
    )
    return ok(res)
