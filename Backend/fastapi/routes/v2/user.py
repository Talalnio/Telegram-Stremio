from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from Backend.fastapi.mappers.v2_mappers import (
    base_url_for_links,
    make_stremio_masked,
    to_user_profile,
)
from Backend.fastapi.schemas.v2_schemas import (
    ApiSuccess,
    LibraryAddRequest,
    UserFullSettings,
    ok,
)
from Backend.fastapi.security.v2_jwt import CurrentUser, require_user
from Backend.fastapi.v2_utils import tracking_db

router = APIRouter(tags=["V2 — User (Library / Settings / Tokens)"])


@router.get("/library")
async def library_get(current: CurrentUser = Depends(require_user)) -> ApiSuccess:
    from Backend.fastapi.mappers.v2_mappers import to_media_summary
    tdb = tracking_db()
    if tdb is None:
        return ok({"items": [], "pagination": {"count": 0}})
    doc = await tdb["subscribers"].find_one({"_id": current.user_id})
    if not doc:
        return ok({"items": [], "pagination": {"count": 0}})
    items_ref = list(doc.get("library") or [])[-200:]
    count = len(items_ref)
    items = []
    for ref in items_ref:
        try:
            tmdb_id = int(ref.get("tmdb_id") or 0)
            db_index = int(ref.get("db_index") or 1)
            media_type = str(ref.get("media_type") or "movie")
            collection = "tv_shows" if media_type in ("tv", "series", "tv_show") else "movies"
            media_doc = await tdb[collection].find_one({"tmdb_id": tmdb_id, "db_index": db_index})
            if media_doc is None:
                fallback = {
                    "tmdb_id": tmdb_id,
                    "db_index": db_index,
                    "media_type": media_type,
                    "title": f"Media {tmdb_id}:{db_index}",
                    "poster": None,
                    "backdrop": None,
                    "rating": None,
                    "release_year": None,
                    "genres": [],
                    "telegram": [],
                    "is_anime": False,
                    "seasons": [] if media_type == "series" else None,
                }
                items.append(to_media_summary(fallback, media_type_override=media_type, in_library=True))
            else:
                raw_type = media_doc.get("media_type") or media_type
                items.append(to_media_summary(media_doc, media_type_override=raw_type, in_library=True))
        except Exception:
            continue
    return ok({"items": items, "pagination": {"count": count}})


@router.post("/library", status_code=201)
async def library_add(
    body: LibraryAddRequest,
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        raise HTTPException(status_code=503, detail="DB_UNAVAILABLE")
    entry = {
        "tmdb_id": int(body.tmdb_id),
        "db_index": int(body.db_index),
        "media_type": body.media_type,
        "added_at": datetime.now(timezone.utc),
    }
    await tdb["subscribers"].update_one(
        {"_id": current.user_id},
        {
            "$addToSet": {"library": entry},
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
    return ok({"added": True, "entry": entry})


@router.delete("/library/{tmdb_id}/{db_index}/{media_type}")
async def library_del(
    tmdb_id: int,
    db_index: int,
    media_type: str,
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        return ok({"removed": False})
    await tdb["subscribers"].update_one(
        {"_id": current.user_id},
        {
            "$pull": {
                "library": {
                    "tmdb_id": int(tmdb_id),
                    "db_index": int(db_index),
                    "media_type": media_type,
                }
            }
        },
    )
    return ok({"removed": True})


@router.get("/continue-watching")
async def continue_watching(current: CurrentUser = Depends(require_user)) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        return ok({"items": []})
    doc = await tdb["subscribers"].find_one({"_id": current.user_id}) or {}
    progress = doc.get("watch_progress") or {}
    items: List[Dict[str, Any]] = []
    for k, v in sorted(
        progress.items(),
        key=lambda x: str(x[1].get("updated_at") or ""),
        reverse=True,
    )[:24]:
        try:
            parts = k.split(":")
            media_type, tmdb, dbidx = parts[0], parts[1], parts[2]
            season = episode = None
            if len(parts) == 5:
                season, episode = int(parts[3]), int(parts[4])
            items.append({
                "key": k,
                "media_type": media_type,
                "tmdb_id": int(tmdb),
                "db_index": int(dbidx),
                "season": season,
                "episode": episode,
                "progress_percent": round(float(v.get("pct") or 0) * 100, 1),
                "current_time": float(v.get("current_time") or 0),
                "last_updated": v.get("updated_at"),
            })
        except Exception:
            continue
    return ok({"items": items})


@router.get("/settings")
async def get_settings(
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess[UserFullSettings]:
    tdb = tracking_db()
    if tdb is None:
        raise HTTPException(status_code=503, detail="DB_UNAVAILABLE")
    doc = await tdb["subscribers"].find_one({"_id": current.user_id}) or {}
    user_set = doc.get("settings") or {}
    playback_set = user_set.get("playback") or {}
    app_set = user_set.get("appearance") or {}
    limits = doc.get("limits") or {}

    pat_list: List[Dict[str, Any]] = []
    try:
        tokens_col = tdb.get_collection("api_tokens")
        if tokens_col is not None:
            async for t in tokens_col.find({"user_id": current.user_id}).limit(20):
                daily_bytes = int(t.get("daily_limit_bytes") or 0)
                monthly_bytes = int(t.get("monthly_limit_bytes") or 0)
                pat_list.append({
                    "id": str(t.get("_id") or t.get("id") or secrets.token_hex(4)),
                    "name": str(t.get("name") or "Untitled Token"),
                    "token_prefix": "tgs_",
                    "created_at": t.get("created_at"),
                    "last_used_at": t.get("last_used_at"),
                    "expires_at": t.get("expires_at"),
                    "limits": {
                        "daily_limit_gb": round(daily_bytes / 1e9, 2) if daily_bytes else 0.0,
                        "monthly_limit_gb": round(monthly_bytes / 1e9, 2) if monthly_bytes else 0.0,
                    },
                    "usage": {"daily_gb": 0, "monthly_gb": 0},
                })
    except Exception:
        pat_list = []

    stremio_token_enc = doc.get("stremio_token_enc") or doc.get("stremio_token") or None
    stremio_configured = bool(stremio_token_enc)
    base = base_url_for_links()
    manifest_url = None
    if stremio_configured and base:
        manifest_url = (
            base + f"/stremio/{make_stremio_masked(str(stremio_token_enc))}/manifest.json"
        )

    daily_limit_bytes = int(limits.get("daily_limit_bytes") or 0)
    monthly_limit_bytes = int(limits.get("monthly_limit_bytes") or 0)

    return ok(UserFullSettings(
        profile=to_user_profile(doc),
        playback={
            "default_quality": playback_set.get("default_quality", "auto"),
            "default_subtitle": playback_set.get("default_subtitle", "ar"),
            "subtitle_style": playback_set.get("subtitle_style", {}),
            "auto_play_next_episode": bool(playback_set.get("auto_play_next_episode", True)),
            "skip_intro_auto": bool(playback_set.get("skip_intro_auto", True)),
            "skip_credits_auto": bool(playback_set.get("skip_credits_auto", False)),
            "playback_speed": float(playback_set.get("playback_speed", 1.0)),
        },
        integrations={
            "stremio_token": {
                "configured": stremio_configured,
                "token_masked": (
                    make_stremio_masked(str(stremio_token_enc)) if stremio_token_enc else None
                ),
                "added_at": doc.get("stremio_added_at"),
                "addon_manifest_url": manifest_url,
            },
            "personal_access_tokens": pat_list,
            "debrid_services": {
                "real_debrid": {"configured": False},
                "premiumize_me": {"configured": False},
            },
        },
        appearance={
            "theme": app_set.get("theme", "pitch_black"),
            "accent_color": app_set.get("accent_color", "purple"),
            "poster_density": app_set.get("poster_density", "default"),
        },
        notifications=doc.get("notifications", {}),
        subscription={
            "status": str(doc.get("subscription_status") or "inactive"),
            "plan": {
                "id": None,
                "name": str(doc.get("subscription_plan_name") or "Current Plan"),
                "price_usd": doc.get("subscription_price_usd"),
                "days": None,
            },
            "expires_at": doc.get("subscription_expiry"),
            "limits": {
                "streams_concurrent": int(limits.get("streams_concurrent") or 2),
                "daily_gb": round(daily_limit_bytes / 1e9, 2) if daily_limit_bytes else 20.0,
                "monthly_gb": round(monthly_limit_bytes / 1e9, 2) if monthly_limit_bytes else 500.0,
            },
            "usage_current_period": {"daily_gb": 0, "monthly_gb": 0},
        },
        sessions=[{
            "id": "sess_curr",
            "device": "Current Browser",
            "ip": None,
            "country": None,
            "last_active_at": datetime.now(timezone.utc),
            "is_current": True,
        }],
    ))


@router.post("/integrations/stremio-token")
async def stremio_token_save(
    body: Dict[str, Any],
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    token = body.get("token")
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="MISSING_TOKEN")
    tdb = tracking_db()
    if tdb is None:
        raise HTTPException(status_code=503, detail="DB_UNAVAILABLE")
    await tdb["subscribers"].update_one(
        {"_id": current.user_id},
        {
            "$set": {
                "stremio_token_enc": token,
                "stremio_added_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return ok({"configured": True})


@router.delete("/integrations/stremio-token")
async def stremio_token_delete(
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        return ok({"removed": False})
    await tdb["subscribers"].update_one(
        {"_id": current.user_id},
        {"$unset": {"stremio_token_enc": "", "stremio_added_at": ""}},
    )
    return ok({"removed": True})


@router.post("/personal-access-tokens", status_code=201)
async def pat_create(
    body: Dict[str, Any],
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        raise HTTPException(status_code=503, detail="TOKENS_COLLECTION_MISSING")
    tokens_col = tdb.get_collection("api_tokens")
    name = str(body.get("name") or "New Token")
    days_valid = int(body.get("days_valid") or 365)
    daily_gb = float(body.get("daily_limit_gb") or 50)
    monthly_gb = float(body.get("monthly_limit_gb") or 1000)
    raw = "tgs_" + secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": current.user_id,
        "name": name,
        "token": raw,
        "token_prefix": "tgs_",
        "created_at": now,
        "last_used_at": None,
        "expires_at": now + timedelta(days=days_valid),
        "daily_limit_bytes": int(daily_gb * 1e9),
        "monthly_limit_bytes": int(monthly_gb * 1e9),
    }
    await tokens_col.insert_one(doc)
    return ok({
        "id": str(doc.get("_id")),
        "token": raw,
        "token_prefix": "tgs_",
        "name": name,
        "expires_at": doc["expires_at"],
    })


@router.delete("/personal-access-tokens/{token_id}")
async def pat_delete(
    token_id: str,
    current: CurrentUser = Depends(require_user),
) -> ApiSuccess:
    tdb = tracking_db()
    if tdb is None:
        return ok({"deleted": False})
    tokens_col = tdb.get_collection("api_tokens")
    from bson import ObjectId
    try:
        oid: Any = ObjectId(token_id)
    except Exception:
        oid = token_id
    await tokens_col.delete_one({"_id": oid, "user_id": current.user_id})
    return ok({"deleted": True})
