import asyncio
import hashlib
import re
from datetime import datetime
from urllib.parse import urljoin
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response, StreamingResponse
from Backend.fastapi.security.credentials import require_auth
from Backend.fastapi.security.tokens import verify_token
from Backend.fastapi.security.two_factor import check_csrf, csrf_token
from Backend.helper import external_addons as addons
from Backend.helper.addon_http import open_public, AddonHTTPError

class MediaErrorRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def guarded(request):
            try:
                return await handler(request)
            except HTTPException as error:
                if not request.url.path.startswith("/addon-media/"):
                    raise
                return Response(status_code=error.status_code, media_type="text/plain", headers={"Cache-Control": "no-store", **(error.headers or {})})
        return guarded

class RelayLease:
    """Own one slot and socket; cleanup is synchronous and safe to repeat."""
    def __init__(self, slots):
        self.slots = slots
        self.connection = self.upstream = None
        self.closed = False

    def bind(self, connection, upstream):
        self.connection, self.upstream = connection, upstream

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Wake any blocking worker before closing its HTTP response.
        import socket
        try:
            raw = getattr(getattr(self.upstream, "fp", None), "raw", None)
            sock = getattr(self.connection, "sock", None)
            if sock is None:
                sock = getattr(raw, "_sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # Closing the raw socket/file wakes a worker even when shutdown
                # alone does not interrupt its buffered read (notably Windows).
                for resource in (sock, raw):
                    if resource is not None:
                        try:
                            resource.close()
                        except Exception:
                            pass
            for resource in (self.upstream, self.connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass
        finally:
            self.slots.release()

class RelayResponse(StreamingResponse):
    def __init__(self, *args, lease, **kwargs):
        super().__init__(*args, **kwargs)
        self.lease = lease

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also covers disconnect/send failure before the iterator starts.
            self.lease.close()
            close_iterator = getattr(self.body_iterator, "aclose", None)
            if close_iterator:
                try:
                    await close_iterator()
                except Exception:
                    pass

router = APIRouter(route_class=MediaErrorRoute)
relay_slots = asyncio.Semaphore(16)
asset_slots = asyncio.Semaphore(8)

def private(data):
    return JSONResponse(data, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})

def subtitle_body(body):
    """Unpack one subtitle within a fixed memory limit; never extract to disk."""
    import gzip
    import io
    import zipfile
    limit = 4 * 1024 * 1024
    if body.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
            body = compressed.read(limit + 1)
    elif body.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            files = [f for f in archive.infolist() if not f.is_dir() and f.filename.lower().endswith(".srt")]
            if len(files) != 1 or files[0].file_size > limit:
                raise AddonHTTPError("Invalid subtitle archive")
            with archive.open(files[0]) as compressed:
                body = compressed.read(limit + 1)
    if len(body) > limit or not body.strip():
        raise AddonHTTPError("Subtitle too large or empty")
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        body = body.decode("utf-16").encode("utf-8")
    elif body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    if len(body) > limit:
        raise AddonHTTPError("Subtitle too large")
    if not (b"-->" in body or b"Dialogue:" in body):
        raise AddonHTTPError("Invalid subtitle response")
    return body

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
    ident = ident.split(".", 1)[0]
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
    slots = asset_slots if config.get("kind") in ("subtitle", "image") or request.url.path.endswith((".srt", ".vtt", ".ass", ".ssa")) else relay_slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        raise HTTPException(429, "Media relay busy")
    lease = RelayLease(slots)
    opening = asyncio.create_task(asyncio.to_thread(open_public, config["url"], headers, request.method))
    try:
        connection, upstream, final_url = await asyncio.shield(opening)
        lease.bind(connection, upstream)
    except asyncio.CancelledError:
        # Seeking can cancel a request while its blocking connection is opening.
        # Release the slot only after that worker has closed its eventual socket.
        def close_pending(future):
            try:
                connection, upstream, _ = future.result()
                lease.bind(connection, upstream)
            except BaseException:
                pass
            finally:
                lease.close()
        opening.add_done_callback(close_pending)
        raise
    except Exception:
        lease.close()
        raise HTTPException(502, "Media source unavailable") from None
    try:
        response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"}
        for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            value = upstream.getheader(key)
            if value:
                response_headers[key] = value
        if upstream.status == 416:
            lease.close()
            return Response(status_code=416, headers={k: v for k, v in response_headers.items() if k != "Content-Length"})
        if upstream.status not in (200, 206):
            lease.close()
            raise HTTPException(502, "Media source unavailable")
        if "json" in (upstream.getheader("Content-Type") or "").lower():
            lease.close()
            raise HTTPException(502, "Invalid media content type")
        if request.method == "HEAD":
            lease.close()
            return Response(status_code=upstream.status, headers=response_headers)
        try:
            if config.get("kind") == "subtitle":
                body = await asyncio.to_thread(upstream.read, 4 * 1024 * 1024 + 1)
                if len(body) > 4 * 1024 * 1024:
                    raise AddonHTTPError("Subtitle too large")
                body = await asyncio.to_thread(subtitle_body, body)
                extension = request.url.path.rsplit(".", 1)[-1].lower()
                mime = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa", "ssa": "text/x-ssa"}.get(extension, "application/x-subrip")
                lease.close()
                return Response(body, media_type=mime, headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})
            prefix = await asyncio.to_thread(upstream.read1, 4096)
            if len(prefix) < 7:
                prefix += await asyncio.to_thread(upstream.read, 7 - len(prefix))
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
                lease.close()
                return Response("\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"})
        except BaseException as error:
            lease.close()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise HTTPException(502, "Invalid media response") from None
        async def chunks():
            pending = 0
            try:
                if prefix:
                    pending += len(prefix)
                    yield prefix
                while not await request.is_disconnected():
                    chunk = await asyncio.to_thread(upstream.read1, 64 * 1024)
                    if not chunk:
                        break
                    pending += len(chunk)
                    if pending >= 8 * 1024 * 1024:
                        try:
                            await asyncio.wait_for(addons.db.update_token_usage(token, pending), timeout=2)
                            pending = 0
                        except Exception:
                            pass
                    yield chunk
            finally:
                lease.close()
                if pending:
                    try:
                        await asyncio.wait_for(addons.db.update_token_usage(token, pending), timeout=2)
                    except Exception:
                        pass
        return RelayResponse(chunks(), lease=lease, status_code=upstream.status, headers=response_headers)
    except BaseException:
        lease.close()
        raise
