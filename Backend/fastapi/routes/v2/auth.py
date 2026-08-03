from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status

from Backend.fastapi.mappers.v2_mappers import to_user_profile
from Backend.fastapi.schemas.v2_schemas import (
    ApiError,
    ApiSuccess,
    AuthResponse,
    RefreshRequest,
    TokenLoginRequest,
    fail,
    ok,
)
from Backend.fastapi.security.tokens import verify_token
from Backend.fastapi.security.v2_jwt import (
    CurrentUser,
    create_access_token,
    create_refresh_token,
    drop_refresh_token,
    find_user_by_telegram_id,
    require_user,
    rotate_refresh_token,
    validate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["V2 — Authentication"])


@router.post("/token")
async def login_with_access_token(body: TokenLoginRequest, request: Request):
    log = logging.getLogger("tg-stremio.v2.auth.token")
    raw = (body.token or "").strip()
    if not raw:
        return fail("MISSING_TOKEN", "Access token is required.", status=400)
    try:
        token_data = await verify_token(raw)
    except HTTPException as he:
        status_code = he.status_code or 401
        if status_code == 401:
            return fail("INVALID_TOKEN", "Invalid or expired access token.", status=401)
        return fail("TOKEN_CHECK_FAILED", str(he.detail or "Token check failed."), status=status_code)
    except Exception as e:  # noqa: BLE001
        log.error("token verify UNEXPECTED error type=%s err=%s", type(e).__name__, str(e))
        return fail("TOKEN_CHECK_FAILED", f"{type(e).__name__}: {str(e)}", status=500)

    token_user_id_raw = token_data.get("user_id")
    token_user_id: int | None = None
    try:
        if token_user_id_raw is not None:
            token_user_id = int(token_user_id_raw)
    except (TypeError, ValueError):
        token_user_id = None

    subscriber: Dict[str, Any] | None = None
    if token_user_id:
        try:
            subscriber = await find_user_by_telegram_id(token_user_id)
        except Exception as e:  # noqa: BLE001
            log.warning("token find user by id failed: %s", e)
            subscriber = None

    if not subscriber:
        try:
            from Backend.fastapi.security.v2_jwt import tracking_db as _tracking_db, _now_utc
            from datetime import timedelta

            tdb = _tracking_db()
            if tdb is None:
                return fail("TRACKING_DB_UNAVAILABLE", "Tracking database is not available.", status=500)

            fallback_uid = token_user_id or 0
            if not fallback_uid:
                import random
                fallback_uid = 10_000_000 + random.randint(1, 99_999_999)
                while await tdb["subscribers"].find_one({"_id": fallback_uid}):
                    fallback_uid = 10_000_000 + random.randint(1, 99_999_999)

            token_label = token_data.get("label") or token_data.get("name") or None
            display_from_label = isinstance(token_label, str) and token_label.strip()
            display_name = token_label.strip() if display_from_label else f"Token User {str(raw)[:8]}"

            subs_status = "active"
            sub_expires = None
            try:
                if token_data.get("subscription_expired"):
                    subs_status = "expired"
                expires_raw = token_data.get("expires_at") or token_data.get("expires")
                if expires_raw:
                    from datetime import datetime

                    if isinstance(expires_raw, datetime):
                        sub_expires = expires_raw
                    elif isinstance(expires_raw, str):
                        try:
                            sub_expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                        except Exception:
                            sub_expires = None
                    elif isinstance(expires_raw, (int, float)):
                        try:
                            sub_expires = datetime.utcfromtimestamp(float(expires_raw))
                        except Exception:
                            sub_expires = None
                    if sub_expires and sub_expires < _now_utc():
                        subs_status = "expired"
            except Exception:
                pass

            now = _now_utc()
            doc = {
                "_id": int(fallback_uid),
                "telegram_user_id": int(token_user_id or fallback_uid),
                "first_name": None,
                "last_name": None,
                "display_name": display_name,
                "username": None,
                "photo_url": None,
                "email": None,
                "password_hash": None,
                "subscription_status": subs_status,
                "subscription_expires_at": sub_expires,
                "is_admin": bool(token_data.get("is_admin", False)),
                "daily_limit_bytes": token_data.get("limits", {}).get("daily_bytes"),
                "monthly_limit_bytes": token_data.get("limits", {}).get("monthly_bytes"),
                "access_token": raw,
                "created_at": now,
                "updated_at": now,
            }
            try:
                await tdb["subscribers"].insert_one(doc)
            except Exception:
                pass
            subscriber = doc
        except Exception as e:  # noqa: BLE001
            log.error("token auto-create subscriber UNEXPECTED error type=%s err=%s", type(e).__name__, str(e))
            return fail("TOKEN_LOGIN_FAILED", f"Failed to provision user from token: {type(e).__name__}", status=500)

    try:
        uid = int(subscriber.get("_id"))
    except Exception:
        return fail("TOKEN_LOGIN_FAILED", "Invalid subscriber id.", status=500)

    access, _ = create_access_token(uid)
    refresh, _, _ = create_refresh_token(uid)
    profile = to_user_profile(subscriber)
    if bool(token_data.get("is_admin", False)) and not profile.is_admin:
        profile.is_admin = True
    return ok(AuthResponse(user=profile, access_token=access, refresh_token=refresh))


@router.post("/refresh")
async def refresh(body: RefreshRequest):
    uid = validate_refresh_token(body.refresh_token)
    if not uid:
        return fail("INVALID_REFRESH_TOKEN", "Refresh token invalid or expired.", status=401)
    new_refresh = rotate_refresh_token(body.refresh_token, uid)
    access, _ = create_access_token(uid)
    return ok({
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
    })


@router.get("/me")
async def get_me(current: CurrentUser = Depends(require_user)):
    return ok(to_user_profile(current.user_doc))


@router.post("/logout")
async def logout(body: RefreshRequest | None = None, current: CurrentUser = Depends(require_user)) -> ApiSuccess:
    if body and body.refresh_token:
        drop_refresh_token(body.refresh_token)
    return ok({"logged_out": True})
