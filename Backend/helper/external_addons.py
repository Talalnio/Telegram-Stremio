"""Protocol bridge. Secrets and upstream identities stay in the tracking database."""
import asyncio
import base64
import hashlib
import json
import secrets
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit
from cryptography.fernet import Fernet
from Backend import db
from Backend.helper.settings_manager import SettingsManager
from Backend.helper.addon_http import fetch_json, parse_public_url, AddonHTTPError

SUPPORTED = {"stream", "catalog", "meta", "subtitles"}
MAX_ADDONS = 10
_network_slots = asyncio.Semaphore(12)
_indexes_ready = False

def cipher():
    secret = SettingsManager.current().session_secret
    if not secret:
        raise AddonHTTPError("Session key unavailable")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("external-addons-v1:" + secret).encode()).digest()))

def collection(name="external_addons"):
    return db.dbs["tracking"][name]

def seal(value):
    return cipher().encrypt(json.dumps(value).encode()).decode()

def unseal(value):
    return json.loads(cipher().decrypt(value.encode()))

async def ready():
    global _indexes_ready
    if not _indexes_ready:
        await collection("external_addon_ids").create_index("expires", expireAfterSeconds=0)
        await collection("external_addon_tickets").create_index("expires", expireAfterSeconds=0)
        _indexes_ready = True

async def entries():
    return await collection().find({"enabled": True}).limit(MAX_ADDONS).to_list(MAX_ADDONS)

def supports(manifest, resource, media_type, content_id=""):
    for spec in manifest.get("resources", []):
        spec = {"name": spec} if isinstance(spec, str) else spec
        if not isinstance(spec, dict) or spec.get("name") != resource:
            continue
        if media_type not in spec.get("types", manifest.get("types", [])):
            continue
        prefixes = spec.get("idPrefixes", manifest.get("idPrefixes"))
        if resource == "catalog" or not prefixes or any(content_id.startswith(p) for p in prefixes if isinstance(p, str)):
            return True
    return False

async def add(url, name, features):
    parse_public_url(url)
    if not url.endswith("/manifest.json"):
        raise AddonHTTPError("Manifest URL must end with /manifest.json")
    if await collection().count_documents({}) >= MAX_ADDONS:
        raise AddonHTTPError("Addon limit reached")
    fingerprint = hashlib.sha256(url.encode()).hexdigest()
    if await collection().find_one({"fingerprint": fingerprint}):
        raise AddonHTTPError("Addon already configured")
    manifest = await fetch_json(url)
    if not isinstance(manifest.get("resources"), list) or not isinstance(manifest.get("types"), list):
        raise AddonHTTPError("Invalid manifest")
    if len(manifest.get("catalogs", [])) > 100:
        raise AddonHTTPError("Too many catalogs")
    advertised = {spec if isinstance(spec, str) else spec.get("name") for spec in manifest["resources"] if isinstance(spec, (str, dict))}
    selected = list(dict.fromkeys(f for f in features if isinstance(f, str) and f in SUPPORTED and f in advertised))
    # Metadata is necessary for externally supplied catalogs.
    if "catalog" in selected and "meta" in advertised and "meta" not in selected:
        selected.append("meta")
    if not selected:
        raise AddonHTTPError("Choose at least one resource")
    ident = secrets.token_hex(8)
    await collection().insert_one({"_id": ident, "name": name[:80] or "إضافة خارجية", "enabled": True,
                                  "features": selected, "fingerprint": fingerprint, "private": seal({"base": url[:-14], "manifest": manifest})})
    return ident

async def catalogs():
    result = []
    for addon in await entries():
        if "catalog" not in addon["features"]:
            continue
        private = unseal(addon["private"])
        for i, cat in enumerate(private["manifest"].get("catalogs", [])):
            if not isinstance(cat, dict) or not isinstance(cat.get("type"), str) or not isinstance(cat.get("id"), str):
                continue
            item = {"id": f"xadd_{addon['_id']}_{i}", "type": cat["type"], "name": str(cat.get("name") or addon["name"])[:160]}
            item["extra"] = [{k: e[k] for k in ("name", "isRequired", "options", "optionsLimit") if k in e}
                             for e in cat.get("extra", []) if isinstance(e, dict) and isinstance(e.get("name"), str)][:15]
            result.append(item)
    return result

async def content_types():
    types = {"movie", "series"}
    for addon in await entries():
        try:
            types.update(t for t in unseal(addon["private"])["manifest"].get("types", []) if isinstance(t, str) and len(t) < 80)
        except Exception:
            continue
    return sorted(types)

async def identity(addon_id, media_type, upstream_id):
    key = hashlib.sha256((addon_id + "\0" + media_type + "\0" + upstream_id).encode()).hexdigest()[:32]
    ident = "xadd:" + key
    await ready()
    await collection("external_addon_ids").update_one({"_id": ident}, {"$set": {
        "addon": addon_id, "expires": datetime.utcnow() + timedelta(days=30), "private": seal({"type": media_type, "id": upstream_id})}}, upsert=True)
    return ident

async def resolve(ident):
    reference = await collection("external_addon_ids").find_one({"_id": ident})
    if not reference:
        return None, None
    addon = await collection().find_one({"_id": reference["addon"], "enabled": True})
    return addon, unseal(reference["private"]) if addon else None

async def ticket(addon, token, url, headers=None, kind="media"):
    extension = ""
    if kind == "subtitle":
        candidate = urlsplit(url).path.rsplit(".", 1)[-1].lower()
        extension = "." + candidate if candidate in ("srt", "vtt", "ass", "ssa") else ".srt"
    parse_public_url(url)
    key = hashlib.sha256((addon + "\0" + token + "\0" + kind + "\0" + url + "\0" + json.dumps(headers or {}, sort_keys=True)).encode()).hexdigest()
    await ready()
    await collection("external_addon_tickets").update_one({"_id": key}, {"$set": {"addon": addon, "owner": hashlib.sha256(token.encode()).hexdigest(),
        "expires": datetime.utcnow() + timedelta(hours=24), "private": seal({"url": url, "headers": headers or {}, "kind": kind})}}, upsert=True)
    return f"{SettingsManager.current().base_url.rstrip('/')}/addon-media/{token}/{key}{extension}"

async def safe_meta(addon, token, meta, media_type):
    result = {k: meta[k] for k in ("name", "type", "description", "releaseInfo", "imdbRating", "genres", "runtime", "released") if k in meta}
    for field in ("name", "description"):
        if isinstance(result.get(field), str):
            result[field] = re.sub(r"https?://\S+", "", result[field])[:10000]
    result["type"] = str(meta.get("type") or media_type)
    result["id"] = await identity(addon, media_type, str(meta["id"]))
    for field in ("poster", "background", "logo"):
        if isinstance(meta.get(field), str) and meta[field]:
            try:
                result[field] = await ticket(addon, token, meta[field])
            except AddonHTTPError:
                pass
    videos = []
    for video in meta.get("videos", [])[:1000]:
        if not isinstance(video, dict) or not video.get("id"):
            continue
        item = {k: video[k] for k in ("title", "season", "episode", "released", "overview") if k in video}
        item["id"] = await identity(addon, media_type, str(video["id"]))
        videos.append(item)
    if videos:
        result["videos"] = videos
    return result

async def request(addon, resource, media_type, ident, extra=None):
    private = unseal(addon["private"])
    if media_type == "collections" and resource != "catalog" and supports(private["manifest"], resource, "collection", ident):
        media_type = "collection"
    if resource not in addon["features"] or not supports(private["manifest"], resource, media_type, ident):
        return {}
    path = "/".join(quote(str(v), safe="") for v in (resource, media_type, ident))
    if extra:
        path += "/" + quote(extra, safe="=&%")
    async with _network_slots:
        return await asyncio.wait_for(fetch_json(private["base"] + "/" + path + ".json"), timeout=20)

def stream_labels(item, formatter=None):
    hints = item.get("behaviorHints") or {}
    if formatter:
        filename = str(hints.get("filename") or "")
        text = re.sub(r"https?://\S+", "", str(item.get("name") or "") + " " + str(item.get("description") or item.get("title") or ""))
        size_match = re.search(r"(?i)\d+(?:\.\d+)?\s*(?:GiB|MiB|GB|MB|KB)", text)
        size = size_match.group(0) if size_match else ""
        if not size and isinstance(hints.get("videoSize"), (int, float)) and hints["videoSize"] > 0:
            size = f"{hints['videoSize'] / 1024 ** 2:.2f}MB"
        name, title = formatter(filename or "Release.2024." + text, "", size)
        title = "\n".join(line for line in title.splitlines() if "Unknown size" not in line)
        return name, (title + "\n" if title else "") + "مصدر خارجي"
    text = str(item.get("name") or "") + " " + str(item.get("description") or item.get("title") or "")
    resolution = re.search(r"(?i)(?<![a-z0-9])(?:2160p|1440p|1080p|720p|480p|360p|4k|8k)(?![a-z0-9])", text)
    tags = []
    for pattern, label in ((r"web[ ._-]?dl", "WEB-DL"), (r"webrip", "WEBRip"),
                           (r"remux", "REMUX"), (r"blu[ ._-]?ray", "BluRay"),
                           (r"hevc|x265|h[ .]?265", "HEVC"), (r"avc|x264|h[ .]?264", "AVC"),
                           (r"av1", "AV1"), (r"dolby vision|\bdv\b", "Dolby Vision"),
                           (r"hdr10\+", "HDR10+"), (r"atmos", "Atmos")):
        if re.search(pattern, text, re.I):
            tags.append(label)
    size = re.search(r"(?i)(?<![a-z0-9])\d+(?:\.\d+)?\s*(?:gib|mib|gb|mb)(?![a-z0-9])", text)
    if size:
        tags.append(size.group(0))
    name = "T7cine+" + (" · " + resolution.group(0).upper() if resolution else "")
    return name, " · ".join(tags) or "مصدر خارجي"

async def resource(resource, token, media_type, ident, extra=None, formatter=None):
    result = []
    if ident.startswith("xadd:"):
        addon, original = await resolve(ident)
        sources = [(addon, original["type"], original["id"])] if addon else []
    else:
        sources = [(a, media_type, ident) for a in await entries()]
    async def load(source):
        addon, remote_type, remote_id = source
        try:
            return addon, remote_type, await request(addon, resource, remote_type, remote_id, extra)
        except Exception:
            return addon, remote_type, {}
    for addon, remote_type, data in await asyncio.gather(*(load(source) for source in sources)):
        try:
            if resource == "meta" and isinstance(data.get("meta"), dict) and data["meta"].get("id"):
                return {"meta": await safe_meta(addon["_id"], token, data["meta"], remote_type)}
            for item in data.get("streams" if resource == "stream" else resource, [])[:100]:
                if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                    continue
                if resource == "stream":
                    headers = (item.get("behaviorHints") or {}).get("proxyHeaders", {}).get("request", {})
                    name, title = stream_labels(item, formatter)
                    entry = {"name": name, "title": title, "url": await ticket(addon["_id"], token, item["url"], headers), "behaviorHints": {"notWebReady": True}}
                    hints = item.get("behaviorHints") or {}
                    if re.fullmatch(r"[0-9a-fA-F]{16}", str(hints.get("videoHash") or "")):
                        entry["behaviorHints"]["videoHash"] = hints["videoHash"]
                    if isinstance(hints.get("videoSize"), int) and 0 < hints["videoSize"] < 100 * 1024 ** 4:
                        entry["behaviorHints"]["videoSize"] = hints["videoSize"]
                else:
                    entry = {"id": secrets.token_hex(8), "lang": str(item.get("lang") or "und"), "url": await ticket(addon["_id"], token, item["url"], (item.get("behaviorHints") or {}).get("proxyHeaders", {}).get("request", {}), kind="subtitle")}
                result.append(entry)
        except Exception:
            continue  # Upstream errors must not expose configured URLs or break Telegram.
    seen = set()
    result = [r for r in result if not (r["url"] in seen or seen.add(r["url"]))]
    return {"streams" if resource == "stream" else resource: result} if resource != "meta" else {"meta": {}}

async def catalog(token, media_type, ident, extra=None):
    try:
        _, addon_id, index = ident.split("_")
        addon = await collection().find_one({"_id": addon_id, "enabled": True})
        if not addon or "catalog" not in addon["features"]:
            return {"metas": []}
        cat = unseal(addon["private"])["manifest"]["catalogs"][int(index)]
        if cat["type"] != media_type:
            return {"metas": []}
        data = await request(addon, "catalog", media_type, cat["id"], extra)
        return {"metas": [await safe_meta(addon_id, token, m, media_type) for m in data.get("metas", [])[:100]
                          if isinstance(m, dict) and isinstance(m.get("id"), str)]}
    except Exception:
        return {"metas": []}
