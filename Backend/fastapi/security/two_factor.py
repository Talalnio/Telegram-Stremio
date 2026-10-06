"""Server-side admin sessions and atomic TOTP/recovery verification."""
import base64
import hashlib
import hmac
import io
import secrets
import time
from datetime import datetime, timedelta

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.fernet import Fernet
from fastapi import HTTPException
from pymongo import ReturnDocument

from Backend import db
from Backend.helper.settings_manager import SettingsManager


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _cipher():
    secret = SettingsManager.current().session_secret
    if not secret:
        raise HTTPException(503, "مفتاح حماية الجلسات غير متاح.")
    key = hashlib.sha256(("admin-totp-v1:" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _fingerprint():
    settings = SettingsManager.current()
    return _hash(settings.admin_username + "\0" + settings.admin_password)


def csrf_token(request):
    if not request.session.get("auth_csrf"):
        request.session["auth_csrf"] = secrets.token_urlsafe(32)
    return request.session["auth_csrf"]


def check_csrf(request, token):
    expected = request.session.get("auth_csrf", "")
    if not expected or not hmac.compare_digest(expected, str(token or "")):
        raise HTTPException(403, "انتهت صلاحية الصفحة. حدّثها وحاول مرة أخرى.")


class AdminSecurity:
    def __init__(self, database):
        self.database = database
        self.indexed = False

    def collection(self, name):
        return self.database.dbs["tracking"][name]

    async def ready(self):
        if not self.indexed:
            for name in ("admin_auth_sessions", "admin_auth_limits"):
                await self.collection(name).create_index("expires_at", expireAfterSeconds=0)
            await self.collection("admin_security").update_one(
                {"_id": "admin"}, {"$setOnInsert": {"enabled": False, "version": secrets.token_hex(16)}}, upsert=True
            )
            self.indexed = True

    async def state(self):
        await self.ready()
        return await self.collection("admin_security").find_one({"_id": "admin"})

    async def throttle(self, request, purpose):
        await self.ready()
        now = int(time.time())
        ip = request.client.host if request.client else "unknown"
        # Persisted counters cover restarts and multiple workers. Do not trust raw forwarding headers.
        for scope, limit in (("ip:" + ip, 10), ("admin", 30)):
            key = _hash(f"{purpose}:{scope}:{now // 300}")
            record = await self.collection("admin_auth_limits").find_one_and_update(
                {"_id": key}, {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": datetime.utcnow() + timedelta(minutes=10)}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
            if record["count"] > limit:
                raise HTTPException(429, "محاولات كثيرة. انتظر خمس دقائق ثم حاول مجدداً.", headers={"Retry-After": "300"})

    async def authenticated(self, request):
        sid = request.session.get("admin_sid")
        if not isinstance(sid, str) or len(sid) > 100:
            return False
        state = await self.state()
        record = await self.collection("admin_auth_sessions").find_one({"_id": _hash(sid), "kind": "session"})
        return bool(record and record["expires_at"] > datetime.utcnow()
                    and record.get("version") == state["version"]
                    and record.get("fingerprint") == _fingerprint()
                    and (not state["enabled"] or record.get("mfa")))

    async def issue_session(self, request, state, mfa=False):
        await self.revoke(request)
        sid = secrets.token_urlsafe(32)
        await self.collection("admin_auth_sessions").insert_one({
            "_id": _hash(sid), "kind": "session", "version": state["version"],
            "fingerprint": _fingerprint(), "mfa": mfa,
            "expires_at": datetime.utcnow() + timedelta(hours=12),
        })
        request.session.clear()
        request.session.update(admin_sid=sid, authenticated=True, username=SettingsManager.current().admin_username)
        csrf_token(request)

    async def revoke(self, request):
        for key in ("admin_sid", "admin_challenge"):
            token = request.session.get(key)
            if token:
                await self.collection("admin_auth_sessions").delete_one({"_id": _hash(token)})

    async def start_login(self, request):
        state = await self.state()
        if not state["enabled"]:
            await self.issue_session(request, state)
            return False
        await self.revoke(request)
        token = secrets.token_urlsafe(32)
        await self.collection("admin_auth_sessions").insert_one({
            "_id": _hash(token), "kind": "challenge", "version": state["version"],
            "fingerprint": _fingerprint(), "expires_at": datetime.utcnow() + timedelta(minutes=5),
        })
        request.session.clear()
        request.session["admin_challenge"] = token
        csrf_token(request)
        return True

    async def consume_factor(self, state, code):
        code = str(code or "").strip()
        query = {"_id": "admin", "enabled": True, "version": state["version"]}
        if len(code) == 6 and code.isascii() and code.isdigit():
            secret = _cipher().decrypt(state["secret"].encode()).decode()
            totp = pyotp.TOTP(secret)
            step = int(time.time()) // 30
            for offset in (0, -1, 1):
                candidate = step + offset
                if hmac.compare_digest(totp.at(candidate * 30), code):
                    query["$or"] = [{"last_step": {"$lt": candidate}}, {"last_step": {"$exists": False}}]
                    result = await self.collection("admin_security").update_one(query, {"$set": {"last_step": candidate}})
                    return result.modified_count == 1
            return False
        if len(code) > 100:
            return False
        digest = _hash(code.replace("-", "").upper())
        query["recovery_hashes"] = digest
        result = await self.collection("admin_security").update_one(query, {"$pull": {"recovery_hashes": digest}})
        return result.modified_count == 1

    async def finish_login(self, request, code):
        await self.throttle(request, "factor")
        state = await self.state()
        challenge = request.session.get("admin_challenge", "")
        record = await self.collection("admin_auth_sessions").find_one({"_id": _hash(challenge), "kind": "challenge"})
        if not record or record["expires_at"] <= datetime.utcnow() or record["version"] != state["version"] or record["fingerprint"] != _fingerprint():
            request.session.clear()
            raise HTTPException(401, "انتهت مهلة التحقق. سجّل الدخول مرة أخرى.")
        if not state["enabled"] or not await self.consume_factor(state, code):
            raise HTTPException(400, "الرمز غير صالح أو سبق استخدامه.")
        consumed = await self.collection("admin_auth_sessions").delete_one({"_id": record["_id"]})
        if not consumed.deleted_count:
            raise HTTPException(401, "انتهت مهلة التحقق. سجّل الدخول مرة أخرى.")
        await self.issue_session(request, state, mfa=True)

    async def setup(self, request):
        state = await self.state()
        if state["enabled"]:
            raise HTTPException(409, "المصادقة الثنائية مفعّلة بالفعل.")
        secret = pyotp.random_base32()
        pending = {"secret": _cipher().encrypt(secret.encode()).decode(), "owner": _hash(request.session["admin_sid"]),
                   "expires_at": datetime.utcnow() + timedelta(minutes=10)}
        await self.collection("admin_security").update_one({"_id": "admin", "enabled": False}, {"$set": {"pending": pending}})
        uri = pyotp.TOTP(secret).provisioning_uri(name=SettingsManager.current().admin_username, issuer_name="T7cine+")
        output = io.BytesIO()
        qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(output)
        return {"secret": secret, "qr_svg": output.getvalue().decode()}

    async def enable(self, request, code):
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise HTTPException(400, "أدخل رمز المصادقة المكوّن من ستة أرقام.")
        state = await self.state()
        pending = state.get("pending") or {}
        if state["enabled"] or pending.get("owner") != _hash(request.session["admin_sid"]) or pending.get("expires_at", datetime.min) <= datetime.utcnow():
            raise HTTPException(400, "انتهت مهلة الإعداد. ابدأ التفعيل من جديد.")
        secret = _cipher().decrypt(pending["secret"].encode()).decode()
        totp = pyotp.TOTP(secret)
        now = int(time.time())
        steps = [now // 30 + offset for offset in (0, -1, 1)]
        matching = next((step for step in steps if hmac.compare_digest(totp.at(step * 30), str(code))), None)
        if matching is None:
            raise HTTPException(400, "رمز المصادقة غير صحيح.")
        codes = [secrets.token_hex(8).upper() for _ in range(10)]
        version = secrets.token_hex(16)
        result = await self.collection("admin_security").update_one(
            {"_id": "admin", "enabled": False, "version": state["version"], "pending.secret": pending["secret"]},
            {"$set": {"enabled": True, "secret": pending["secret"], "version": version,
                      "recovery_hashes": [_hash(c) for c in codes], "last_step": matching}, "$unset": {"pending": ""}},
        )
        if not result.modified_count:
            raise HTTPException(409, "تغيّر الإعداد. حدّث الصفحة.")
        await self.issue_session(request, {"version": version}, mfa=True)
        return {"recovery_codes": [c[:8] + "-" + c[8:] for c in codes]}

    async def disable(self, request, code):
        state = await self.state()
        if not state["enabled"] or not await self.consume_factor(state, code):
            raise HTTPException(400, "الرمز غير صالح أو سبق استخدامه.")
        version = secrets.token_hex(16)
        result = await self.collection("admin_security").update_one(
            {"_id": "admin", "version": state["version"], "enabled": True},
            {"$set": {"enabled": False, "version": version}, "$unset": {"secret": "", "pending": "", "recovery_hashes": "", "last_step": ""}},
        )
        if not result.modified_count:
            raise HTTPException(409, "تغيّر الإعداد. حدّث الصفحة.")
        await self.issue_session(request, {"version": version})
        return {"enabled": False}


admin_security = AdminSecurity(db)
