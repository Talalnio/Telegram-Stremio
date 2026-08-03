from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from Backend.config import Telegram
from Backend.fastapi.v2_utils import tracking_db

log = logging.getLogger("tg-stremio.v2.auth")

# ══════════════════════════════════════════════════════════════
# §1 — JWT SECRETS (prefer env, fallback to admin password/bot token)
# ══════════════════════════════════════════════════════════════
try:
    _fallback_1 = str(getattr(Telegram, "ADMIN_PASSWORD", ""))
    _fallback_2 = str(getattr(Telegram, "BOT_TOKEN", ""))
    JWT_SECRET_KEY: str = os.environ.get("V2_JWT_SECRET") or _fallback_1 or _fallback_2 or "tg-stremio-v2-dev-secret-change-me"
except Exception:
    JWT_SECRET_KEY = "tg-stremio-v2-dev-secret-change-me"

JWT_ALGO = "HS256"
ACCESS_TOKEN_TTL: timedelta = timedelta(minutes=int(os.environ.get("V2_JWT_ACCESS_MIN", "15")))
REFRESH_TOKEN_TTL: timedelta = timedelta(days=int(os.environ.get("V2_JWT_REFRESH_DAYS", "30")))

security = HTTPBearer(auto_error=False)
_local_store_lock = threading.Lock()
_REFRESH_STORE: Dict[str, Dict[str, Any]] = {}
_REFRESH_PREFIX = "tgsrf_"


# ══════════════════════════════════════════════════════════════
# §2 — Password hashing (bcrypt)
# ══════════════════════════════════════════════════════════════
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if not (stored_hash or "").startswith("$2"):
            return False
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
# §3 — JWT create / decode
# ══════════════════════════════════════════════════════════════
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int, *, extras: Optional[Dict[str, Any]] = None) -> Tuple[str, datetime]:
    exp = _now_utc() + ACCESS_TOKEN_TTL
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "uid": int(user_id),
        "kind": "access",
        "iat": _now_utc(),
        "exp": exp,
        "jti": secrets.token_urlsafe(12),
    }
    if extras:
        payload.update(extras)
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGO), exp


def create_refresh_token(user_id: int) -> Tuple[str, datetime, str]:
    token_str = _REFRESH_PREFIX + secrets.token_urlsafe(32)
    jti = secrets.token_hex(8)
    exp = _now_utc() + REFRESH_TOKEN_TTL
    with _local_store_lock:
        _REFRESH_STORE[token_str] = {
            "user_id": int(user_id),
            "jti": jti,
            "exp": exp,
            "created": _now_utc(),
        }
    return token_str, exp, jti


def drop_refresh_token(token: str) -> None:
    with _local_store_lock:
        _REFRESH_STORE.pop(token, None)


def rotate_refresh_token(old_token: str, user_id: int) -> str:
    drop_refresh_token(old_token)
    new_tok, _, _ = create_refresh_token(user_id)
    return new_tok


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGO])
        if payload.get("kind") != "access":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def validate_refresh_token(token: str) -> Optional[int]:
    if not str(token).startswith(_REFRESH_PREFIX):
        return None
    with _local_store_lock:
        entry = _REFRESH_STORE.get(token)
    if not entry:
        return None
    exp = entry.get("exp")
    if exp and exp < _now_utc():
        drop_refresh_token(token)
        return None
    return int(entry.get("user_id") or 0) or None


# ══════════════════════════════════════════════════════════════
# §4 — Telegram Login Widget signature validation
# ══════════════════════════════════════════════════════════════
def verify_telegram_widget(payload: Dict[str, Any]) -> bool:
    try:
        import hashlib
        import hmac

        bot_token = (
            os.environ.get("TELEGRAM_BOT_TOKEN")
            or os.environ.get("TG_BOT_TOKEN")
            or getattr(Telegram, "BOT_TOKEN", "") or ""
        )
        if not bot_token:
            return False

        auth_date = int(payload.get("auth_date") or 0)
        if not auth_date:
            return False
        if abs(_now_utc().timestamp() - auth_date) > 86400:
            return False

        payload_copy = {k: v for k, v in payload.items() if v is not None and k != "hash"}
        data_check_arr: list[str] = []
        for k in sorted(payload_copy.keys()):
            data_check_arr.append(f"{k}={payload_copy[k]}")
        data_check_string = "\n".join(data_check_arr)
        secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
        expected = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        given = str(payload.get("hash") or "")
        return hmac.compare_digest(expected.lower(), given.lower())
    except Exception as e:  # noqa: BLE001
        log.warning("telegram widget verify error: %s", e)
        return False


# ══════════════════════════════════════════════════════════════
# §5 — User lookup helpers (tied to existing tracking subscribers collection)
# ══════════════════════════════════════════════════════════════
async def find_user_by_telegram_id(user_id: int) -> Optional[Dict[str, Any]]:
    tdb = tracking_db()
    if tdb is None:
        return None
    q = {"$or": [{"telegram_user_id": int(user_id)}, {"_id": int(user_id)}]}
    return await tdb["subscribers"].find_one(q)


async def find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    tdb = tracking_db()
    if tdb is None:
        return None
    import re
    return await tdb["subscribers"].find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}
    )


async def upsert_user_from_telegram(payload: Dict[str, Any]) -> Dict[str, Any]:
    tdb = tracking_db()
    if tdb is None:
        raise RuntimeError("tracking_db not available")
    telegram_user_id = int(payload.get("id") or 0)
    existing = await find_user_by_telegram_id(telegram_user_id)
    first = payload.get("first_name") or ""
    last = payload.get("last_name") or ""
    display = f"{first} {last}".strip() or f"User {telegram_user_id}"
    username = payload.get("username")
    photo = payload.get("photo_url")
    now = _now_utc()
    default_trial = now + timedelta(days=7)

    if existing:
        patch = {
            "$set": {
                "display_name": existing.get("display_name") or display,
                "first_name": first,
                "last_name": last,
                "username": username,
                "photo_url": photo,
                "updated_at": now,
            }
        }
        await tdb["subscribers"].update_one({"_id": existing.get("_id")}, patch)
        updated = await tdb["subscribers"].find_one({"_id": existing.get("_id")})
        return updated or existing

    default_limit_daily = 20 * 1024 * 1024 * 1024  # 20 GB
    default_limit_monthly = 500 * 1024 * 1024 * 1024  # 500 GB
    doc = {
        "_id": int(telegram_user_id),
        "telegram_user_id": int(telegram_user_id),
        "first_name": first,
        "last_name": last,
        "display_name": display,
        "username": username,
        "photo_url": photo,
        "email": None,
        "password_hash": None,
        "subscription_status": "trial",
        "subscription_expiry": default_trial,
        "limits": {
            "daily_limit_bytes": default_limit_daily,
            "monthly_limit_bytes": default_limit_monthly,
        },
        "stremio_token_enc": None,
        "settings": {
            "playback": {
                "default_quality": "auto",
                "default_subtitle": "ar",
                "auto_play_next_episode": True,
                "skip_intro_auto": True,
            },
            "appearance": {"theme": "pitch_black", "accent": "purple"},
        },
        "library": [],
        "watch_progress": {},
        "created_at": now,
        "updated_at": now,
    }
    await tdb["subscribers"].insert_one(doc)
    return doc


async def create_email_user(
    *,
    email: str,
    password_hash: str,
    display_name: Optional[str],
    telegram_user_id: Optional[int],
) -> Dict[str, Any]:
    tdb = tracking_db()
    if tdb is None:
        raise RuntimeError("tracking_db not available")
    if await find_user_by_email(email):
        raise HTTPException(status_code=409, detail="EMAIL_ALREADY_REGISTERED")

    now = _now_utc()
    default_trial = now + timedelta(days=7)
    default_daily = 20 * 1024 * 1024 * 1024
    default_monthly = 500 * 1024 * 1024 * 1024

    counter = await tdb["counters"].find_one_and_update(
        {"_id": "subscribers"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    new_id = int((counter or {}).get("seq") or 100000000000)

    doc = {
        "_id": new_id,
        "telegram_user_id": telegram_user_id,
        "first_name": None,
        "last_name": None,
        "display_name": display_name or email.split("@", 1)[0],
        "username": None,
        "photo_url": None,
        "email": email.lower(),
        "password_hash": password_hash,
        "subscription_status": "trial",
        "subscription_expiry": default_trial,
        "limits": {
            "daily_limit_bytes": default_daily,
            "monthly_limit_bytes": default_monthly,
        },
        "stremio_token_enc": None,
        "settings": {
            "playback": {
                "default_quality": "auto",
                "default_subtitle": "ar",
                "auto_play_next_episode": True,
                "skip_intro_auto": True,
            },
            "appearance": {"theme": "pitch_black", "accent": "purple"},
        },
        "library": [],
        "watch_progress": {},
        "created_at": now,
        "updated_at": now,
    }
    await tdb["subscribers"].insert_one(doc)
    return doc


# ══════════════════════════════════════════════════════════════
# §6 — FastAPI Dependable: JWT-based current user (async)
# ══════════════════════════════════════════════════════════════
class CurrentUser:
    def __init__(self, user_doc: Optional[Dict[str, Any]]):
        self.user_doc = user_doc or {}
        self.user_id: int = int(self.user_doc.get("_id") or 0)
        self.is_authenticated: bool = bool(user_doc)
        self.is_admin: bool = False
        if user_doc:
            try:
                self.is_admin = self.user_id == int(Telegram.OWNER_ID) or bool(user_doc.get("is_admin"))
            except Exception:
                self.is_admin = bool(user_doc.get("is_admin"))


async def optional_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    if not creds or not creds.credentials:
        return CurrentUser(None)
    payload = decode_access_token(creds.credentials)
    if not payload:
        return CurrentUser(None)
    uid = int(payload.get("uid") or 0)
    if not uid:
        return CurrentUser(None)
    try:
        tdb = tracking_db()
        doc = await tdb["subscribers"].find_one({"_id": uid}) if tdb else None
    except Exception:
        return CurrentUser(None)
    return CurrentUser(doc)


async def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="MISSING_AUTH")
    payload = decode_access_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_OR_EXPIRED_TOKEN")
    uid = int(payload.get("uid") or 0)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="INVALID_TOKEN")
    try:
        tdb = tracking_db()
        if tdb is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DB_UNAVAILABLE")
        doc = await tdb["subscribers"].find_one({"_id": uid})
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DB_UNAVAILABLE")
    if not doc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="USER_NOT_FOUND")
    return CurrentUser(doc)


async def require_admin(current: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="ADMIN_REQUIRED")
    return current


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "validate_refresh_token",
    "rotate_refresh_token",
    "drop_refresh_token",
    "verify_telegram_widget",
    "find_user_by_telegram_id",
    "find_user_by_email",
    "upsert_user_from_telegram",
    "create_email_user",
    "optional_user",
    "require_user",
    "require_admin",
    "CurrentUser",
]
