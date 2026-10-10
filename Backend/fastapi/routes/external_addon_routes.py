import asyncio
import hashlib
import re
from datetime import datetime
from urllib.parse import urljoin
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from Backend.fastapi.security.credentials import require_auth
from Backend.fastapi.security.tokens import verify_token
from Backend.fastapi.security.two_factor import check_csrf, csrf_token
from Backend.helper import external_addons as addons
from Backend.helper.addon_http import open_public, AddonHTTPError

router = APIRouter()
relay_slots = asyncio.Semaphore(16)

def private(data):
    return JSONResponse(data, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})

@router.get("/api/admin/external-addons")
async def list_addons(request: Request, _: bool = Depends(require_auth)):
    rows = await addons.collection().find({}).limit(addons.MAX_ADDONS).to_list(addons.MAX_ADDONS)
    return private({"addons": [{"id": r["_id"], "name": r["name"], "enabled": r["enabled"], "features": r["features"]} for r in rows], "csrf": csrf_token(request)})

@router.post("/api/admin/external-addons")
async def save_addon(request: Request, payload: dict, _: bool = Depends(require_auth)):
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    try:
        ident = await addons.add(str(payload.get("url") or ""), str(payload.get("name") or ""), payload.get("features") or [])
        return private({"id": ident})
    except Exception:
        raise HTTPException(400, "تعذّر التحقق من الإضافة. تحقق من الرابط وتوافق الموارد.") from None

@router.post("/api/admin/external-addons/{ident}/{action}")
async def change_addon(ident: str, action: str, request: Request, _: bool = Depends(require_auth)):
    check_csrf(request, request.headers.get("X-CSRF-Token"))
    if action == "remove":
        await addons.collection().delete_one({"_id": ident})
        await addons.collection("external_addon_ids").delete_many({"addon": ident})
        await addons.collection("external_addon_tickets").delete_many({"addon": ident})
    elif action in ("enable", "disable"):
        await addons.collection().update_one({"_id": ident}, {"$set": {"enabled": action == "enable"}})
    else:
        raise HTTPException(404, "عملية غير معروفة")
    return private({"ok": True})

@router.api_route("/addon-media/{token}/{ident}", methods=["GET", "HEAD"])
async def relay(request: Request, token: str, ident: str, token_data: dict = Depends(verify_token)):
    if token_data.get("subscription_expired") or token_data.get("limit_exceeded"):
        raise HTTPException(403, "Access unavailable")
    row = await addons.collection("external_addon_tickets").find_one({"_id": ident})
    if not row or row["expires"] < datetime.utcnow() or row["owner"] != hashlib.sha256(token.encode()).hexdigest():
        raise HTTPException(404, "Media unavailable")
    if not await addons.collection().find_one({"_id": row["addon"], "enabled": True}):
        raise HTTPException(404, "Addon unavailable")
    config = addons.unseal(row["private"])
    headers = dict(config.get("headers") or {})
    for key in ("Range", "If-Range"):
        if request.headers.get(key):
            headers[key] = request.headers[key]
    try:
        await asyncio.wait_for(relay_slots.acquire(), timeout=0.1)
    except asyncio.TimeoutError:
        raise HTTPException(429, "Media relay busy")
    try:
        connection, upstream, final_url = await asyncio.to_thread(open_public, config["url"], headers, request.method)
    except Exception:
        relay_slots.release()
        raise HTTPException(502, "Media source unavailable") from None
    response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"}
    for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        value = upstream.getheader(key)
        if value:
            response_headers[key] = value
    if upstream.status not in (200, 206):
        upstream.close(); connection.close(); relay_slots.release()
        raise HTTPException(502, "Media source unavailable")
    if request.method == "HEAD":
        upstream.close(); connection.close(); relay_slots.release()
        return Response(status_code=upstream.status, headers=response_headers)
    try:
        prefix = await asyncio.to_thread(upstream.read, 4096)
        is_hls = prefix.lstrip().startswith(b"#EXTM3U")
        if is_hls:
            body = prefix + await asyncio.to_thread(upstream.read, 1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise AddonHTTPError("Playlist too large")
            text = body.decode("utf-8-sig")
            lines = []
            for line in text.splitlines():
                if line and not line.startswith("#"):
                    line = await addons.ticket(row["addon"], token, urljoin(final_url, line), config.get("headers"))
                elif 'URI="' in line:
                    for match in list(re.finditer(r'URI="([^"]+)"', line)):
                        rewritten = await addons.ticket(row["addon"], token, urljoin(final_url, match.group(1)), config.get("headers"))
                        line = line.replace(match.group(0), 'URI="' + rewritten + '"')
                lines.append(line)
            upstream.close(); connection.close(); relay_slots.release()
            return Response("\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})
    except Exception:
        upstream.close(); connection.close(); relay_slots.release()
        raise HTTPException(502, "Invalid media response") from None
    async def chunks():
        pending = 0
        try:
            if prefix:
                pending += len(prefix)
                yield prefix
            while not await request.is_disconnected():
                chunk = await asyncio.to_thread(upstream.read, 256 * 1024)
                if not chunk:
                    break
                pending += len(chunk)
                if pending >= 1024 * 1024:
                    await addons.db.update_token_usage(token, pending)
                    pending = 0
                yield chunk
        finally:
            if pending:
                try:
                    await addons.db.update_token_usage(token, pending)
                except Exception:
                    pass
            upstream.close(); connection.close(); relay_slots.release()
    return StreamingResponse(chunks(), status_code=upstream.status, headers=response_headers)
