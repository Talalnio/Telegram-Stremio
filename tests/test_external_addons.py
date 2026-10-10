import ast
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

class Cursor:
    def __init__(self, rows): self.rows = rows
    def limit(self, count): self.rows = self.rows[:count]; return self
    async def to_list(self, count): return copy.deepcopy(self.rows[:count])

class Collection:
    def __init__(self): self.rows = {}
    def matches(self, row, query): return all(row.get(k) == v for k, v in query.items())
    def find(self, query): return Cursor([r for r in self.rows.values() if self.matches(r, query)])
    async def find_one(self, query):
        return next((copy.deepcopy(r) for r in self.rows.values() if self.matches(r, query)), None)
    async def count_documents(self, query): return len(self.find(query).rows)
    async def insert_one(self, row): self.rows[row["_id"]] = copy.deepcopy(row)
    async def create_index(self, *args, **kwargs): pass
    async def update_one(self, query, update, upsert=False):
        row = await self.find_one(query)
        if not row and not upsert: return
        row = row or dict(query)
        row.update(update.get("$set", {})); self.rows[row["_id"]] = copy.deepcopy(row)
    async def delete_one(self, query):
        for key in list(self.rows):
            if self.matches(self.rows[key], query): del self.rows[key]; break
    async def delete_many(self, query):
        for key in list(self.rows):
            if self.matches(self.rows[key], query): del self.rows[key]

class ExternalAddonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous = {k:v for k,v in sys.modules.items() if k == "Backend" or k.startswith("Backend.")}
        backend = types.ModuleType("Backend"); backend.__path__ = []
        helper = types.ModuleType("Backend.helper"); helper.__path__ = []
        self.collections = {name:Collection() for name in ("external_addons", "external_addon_ids", "external_addon_tickets")}
        backend.db = types.SimpleNamespace(dbs={"tracking": self.collections}, update_token_usage=AsyncMock())
        settings = types.ModuleType("Backend.helper.settings_manager")
        settings.SettingsManager = types.SimpleNamespace(current=lambda: types.SimpleNamespace(session_secret="test-secret", base_url="https://our.example"))
        sys.modules.update({"Backend":backend,"Backend.helper":helper,"Backend.helper.settings_manager":settings})
        self.http = load("Backend.helper.addon_http", ROOT / "Backend/helper/addon_http.py")
        self.addons = load("Backend.helper.external_addons", ROOT / "Backend/helper/external_addons.py")
        self.manifest = {"types":["movie","series"], "resources":["stream","catalog","meta","subtitles"],
                         "idPrefixes":["tt"], "catalogs":[{"id":"private_catalog","type":"movie","name":"Movies"}]}
        self.addons.fetch_json = AsyncMock(return_value=self.manifest)

    def tearDown(self):
        for key in list(sys.modules):
            if key == "Backend" or key.startswith("Backend."): del sys.modules[key]
        sys.modules.update(self.previous)

    async def configure(self):
        return await self.addons.add("https://provider.example/secret-key/manifest.json", "Example", ["stream","catalog","subtitles"])

    async def test_secrets_encrypted_and_duplicates_rejected(self):
        ident = await self.configure()
        row = self.collections["external_addons"].rows[ident]
        self.assertNotIn("secret-key", str(row))
        self.assertIn("meta", row["features"])
        with self.assertRaises(self.http.AddonHTTPError): await self.configure()
        self.assertEqual(len(await self.addons.catalogs()), 1)

    async def test_features_reflect_advertised_resources(self):
        self.manifest["resources"] = ["subtitles"]
        ident = await self.addons.add("https://provider.example/manifest.json", "Subs", ["stream", "catalog", "subtitles"])
        self.assertEqual(self.collections["external_addons"].rows[ident]["features"], ["subtitles"])

    async def test_resource_matching(self):
        manifest = {"types":["movie"], "resources":[{"name":"stream","types":["series"],"idPrefixes":["kitsu:"]}]}
        self.assertTrue(self.addons.supports(manifest,"stream","series","kitsu:12"))
        self.assertFalse(self.addons.supports(manifest,"stream","movie","kitsu:12"))
        self.assertFalse(self.addons.supports(manifest,"stream","series","tt12"))

    async def test_stream_links_hidden_and_bound_to_account(self):
        ident = await self.configure()
        self.addons.fetch_json.return_value = {"streams":[{"url":"https://cdn.example/video?key=private", "behaviorHints":{"proxyHeaders":{"request":{"Authorization":"Bearer secret"}}}}]}
        result = await self.addons.resource("stream","subscriber-a","movie","tt123")
        self.assertEqual(result["streams"][0]["name"], "T7cine+")
        self.assertNotIn("private", str(result)); self.assertNotIn("Bearer", str(result))
        row = next(iter(self.collections["external_addon_tickets"].rows.values()))
        self.assertNotIn("secret", row["private"])
        self.assertEqual(self.addons.unseal(row["private"])["headers"]["Authorization"], "Bearer secret")
        first = result["streams"][0]["url"]
        again = await self.addons.resource("stream","subscriber-a","movie","tt123")
        self.assertEqual(first, again["streams"][0]["url"])
        other = await self.addons.resource("stream","subscriber-b","movie","tt123")
        self.assertNotEqual(first.rsplit("/",1)[1], other["streams"][0]["url"].rsplit("/",1)[1])
        self.collections["external_addons"].rows[ident]["enabled"] = False
        self.assertEqual((await self.addons.resource("stream","subscriber-a","movie","tt123"))["streams"], [])

    async def test_catalog_meta_episode_ids_and_subtitles(self):
        ident = await self.configure()
        self.addons.fetch_json.return_value = {"metas":[{"id":"tt123","type":"movie","name":"Example","poster":"https://cdn.example/secret-image"}]}
        result = await self.addons.catalog("user","movie",f"xadd_{ident}_0")
        meta = result["metas"][0]
        self.assertTrue(meta["id"].startswith("xadd:")); self.assertNotIn("secret-image", meta["poster"])
        self.addons.fetch_json.return_value = {"meta":{"id":"tt123","name":"Example","videos":[{"id":"tt123:1:2","season":1,"episode":2}]}}
        result = await self.addons.resource("meta","user","movie",meta["id"])
        self.assertTrue(result["meta"]["videos"][0]["id"].startswith("xadd:"))
        self.addons.fetch_json.return_value = {"subtitles":[{"id":"x","lang":"ara","url":"https://subs.example/private.srt"}]}
        result = await self.addons.resource("subtitles","user","movie",meta["id"])
        self.assertEqual(result["subtitles"][0]["lang"], "ara")
        self.assertNotIn("private.srt",str(result))

    async def test_external_formatter_uses_filename_description_and_size(self):
        from test_stream_formatter import format_details
        item = {"name": "provider 1080p", "description": "size 1.39GB",
                "behaviorHints": {"filename": "Release.2024.1080p.AMZN.WEB-DL.HEVC.Atmos.DDP5.1.mkv"}}
        name, title = self.addons.stream_labels(item, format_details)
        self.assertEqual(name, "1080p FHD")
        self.assertIn("WEB-DL", title)
        self.assertIn("HEVC", title)
        self.assertIn("Atmos", title)
        self.assertIn("5.1", title)
        self.assertIn("1.39GB · Prime Video", title)
        self.assertEqual(title.splitlines()[-1], "مصدر خارجي")
        item = {"description": "1080p WEB-DL HEVC 10-bit HDR10+ AAC 2.0 750MB"}
        name, title = self.addons.stream_labels(item, format_details)
        self.assertEqual(name, "1080p FHD")
        self.assertIn("10-bit", title)
        self.assertIn("750MB", title)
        self.assertNotIn("Unknown size", self.addons.stream_labels({}, format_details)[1])

    async def test_subtitle_extension_and_ticket_kinds(self):
        ident = await self.configure()
        url = "https://subs.example/download?secret=hidden"
        subtitle = await self.addons.ticket(ident, "user", url, kind="subtitle")
        video = await self.addons.ticket(ident, "user", url)
        self.assertTrue(subtitle.endswith(".srt"))
        self.assertNotEqual(subtitle.rsplit("/", 1)[1].split(".")[0], video.rsplit("/", 1)[1])
        vtt = await self.addons.ticket(ident, "user", "https://subs.example/a.vtt", kind="subtitle")
        self.assertTrue(vtt.endswith(".vtt"))

    async def test_failures_and_unsupported_streams(self):
        await self.configure()
        self.addons.fetch_json.side_effect = self.http.AddonHTTPError("failed")
        self.assertEqual(await self.addons.resource("stream","user","movie","tt123"), {"streams":[]})
        self.addons.fetch_json.side_effect = None
        self.addons.fetch_json.return_value = {"streams":[{"infoHash":"abc"},{"url":"file:///etc/passwd"},{"externalUrl":"https://example.com"}]}
        self.assertEqual(await self.addons.resource("stream","user","movie","tt123"), {"streams":[]})

    def test_subtitle_unpacking_preserves_timing_and_limits(self):
        import gzip
        import io
        import zipfile
        tree = ast.parse((ROOT / "Backend/fastapi/routes/external_addon_routes.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "subtitle_body")
        scope = {"AddonHTTPError": self.http.AddonHTTPError}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "subtitles", "exec"), scope)
        normalize = scope["subtitle_body"]
        original = "1\n00:00:01,000 --> 00:00:02,000\nترجمة\n".encode("utf-8")
        self.assertEqual(normalize(gzip.compress(original)), original)
        self.assertEqual(normalize(original.decode("utf-8").encode("utf-16")), original)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("../../subtitle.srt", original)
        self.assertEqual(normalize(archive.getvalue()), original)
        for content in (b"<html>error</html>", gzip.compress(b"x" * (4 * 1024 * 1024 + 1))):
            with self.assertRaises(self.http.AddonHTTPError): normalize(content)

    def test_public_addresses_and_urls(self):
        for address in ("127.0.0.1","10.0.0.1","169.254.169.254","::1","::ffff:127.0.0.1","224.0.0.1"):
            self.assertFalse(self.http.public_address(address))
        self.assertTrue(self.http.public_address("8.8.8.8"))
        for url in ("file:///etc/passwd","https://user:password@example.com/","https://example.com:8080/","https://example.com/\nheader"):
            with self.assertRaises(self.http.AddonHTTPError): self.http.parse_public_url(url)

    def test_dns_private_destination_blocked(self):
        records=[(2,1,6,"",("127.0.0.1",443))]
        with patch.object(self.http.socket,"getaddrinfo",return_value=records):
            with self.assertRaises(self.http.AddonHTTPError): self.http.open_public("https://example.com")

    async def test_relay_auth_owner_revocation_range_and_hls(self):
        import io
        import re
        import hashlib
        from datetime import datetime
        from urllib.parse import urljoin
        from fastapi import APIRouter, Depends, HTTPException, Request, FastAPI
        from fastapi.responses import JSONResponse, Response, StreamingResponse
        from fastapi.testclient import TestClient
        async def auth(request: Request):
            if request.headers.get("X-Admin") != "yes": raise HTTPException(401)
            return True
        async def verify(token: str):
            if token not in ("user", "other"): raise HTTPException(401)
            return {}
        scope = {"asyncio":asyncio,"hashlib":hashlib,"re":re,"datetime":datetime,"urljoin":urljoin,
                 "Depends":Depends,"HTTPException":HTTPException,"Request":Request,"APIRouter":APIRouter,
                 "JSONResponse":JSONResponse,"Response":Response,"StreamingResponse":StreamingResponse,
                 "require_auth":auth,"verify_token":verify,"addons":self.addons,"AddonHTTPError":self.http.AddonHTTPError,
                 "check_csrf":lambda *a: None,"csrf_token":lambda *a:"test"}
        scope["router"] = APIRouter(); scope["relay_slots"] = asyncio.Semaphore(16)
        calls=[]
        content=[b"video bytes"]
        class Upstream(io.BytesIO):
            status=200
            def read1(self, count): return super().read1(min(count, 3))
            def getheader(self, key): return {"Content-Type":"video/mp4","Content-Length":str(len(content[0]))}.get(key)
        def open_public(url, headers, method):
            calls.append((url,headers,method))
            return types.SimpleNamespace(close=lambda:None), Upstream(content[0]), url
        scope["open_public"] = open_public
        tree=ast.parse((ROOT / "Backend/fastapi/routes/external_addon_routes.py").read_text(encoding="utf-8"))
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),"relay_test","exec"),scope)
        app=FastAPI(); app.include_router(scope["router"])
        ident=await self.configure()
        url=await self.addons.ticket(ident,"user","https://cdn.example/movie.mp4")
        path=url.replace("https://our.example","")
        # TestClient runs requests in its own thread; the in-memory DB has no loop binding.
        client=TestClient(app)
        self.assertEqual(client.get("/api/admin/external-addons").status_code,401)
        response=client.get(path,headers={"Range":"bytes=0-10"})
        self.assertEqual(response.status_code,200); self.assertEqual(response.content,b"video bytes")
        self.assertEqual(calls[-1][1]["Range"],"bytes=0-10")
        self.assertEqual(response.headers["cache-control"],"no-store")
        self.assertEqual(client.get(path.replace("/user/","/other/")).status_code,404)
        self.assertEqual(client.get(path.replace("/user/","/invalid/")).status_code,401)
        content[0]=b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsegment.ts\n'
        response=client.get(path)
        self.assertEqual(response.status_code,200)
        self.assertNotIn("cdn.example",response.text)
        self.assertNotIn('URI="key.bin"',response.text)
        self.assertIn("/addon-media/user/",response.text)
        # Subtitle URLs retain an extension, exact bytes and a usable content type.
        subtitle = await self.addons.ticket(ident, "user", "https://cdn.example/download", kind="subtitle")
        content[0] = b"1\n00:00:01,000 --> 00:00:02,000\nSubtitle\n"
        response = client.get(subtitle.replace("https://our.example", ""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content[0])
        self.assertIn("application/x-subrip", response.headers["content-type"])
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        content[0] = b"<html>upstream error</html>"
        self.assertEqual(client.get(subtitle.replace("https://our.example", "")).status_code, 502)
        Upstream.status = 206
        content[0] = b"range data"
        self.assertEqual(client.get(path).status_code, 206)
        Upstream.status = 416
        self.assertEqual(client.get(path).status_code, 416)
        Upstream.status = 200
        self.collections["external_addons"].rows[ident]["enabled"]=False
        self.assertEqual(client.get(path).status_code,404)
        self.collections["external_addons"].rows[ident]["enabled"]=True
        key=path.rsplit("/",1)[1]
        self.collections["external_addon_tickets"].rows[key]["expires"]=datetime(2000,1,1)
        self.assertEqual(client.get(path).status_code,404)
        self.assertEqual(scope["relay_slots"]._value,16)
        # An aborted seek must not leak the relay slot or the pending socket.
        import threading
        from datetime import timedelta
        opened = threading.Event(); finish = threading.Event(); closed = threading.Event()
        def delayed_open(*args):
            opened.set(); finish.wait(2)
            return types.SimpleNamespace(close=closed.set), Upstream(b"bytes"), "https://cdn.example/movie.mp4"
        scope["open_public"] = delayed_open
        self.collections["external_addon_tickets"].rows[key]["expires"] = datetime.utcnow() + timedelta(hours=1)
        request = Request({"type":"http", "method":"GET", "path":path, "headers":[], "query_string":b""})
        operation = asyncio.create_task(scope["relay"](request, "user", key, {}))
        await asyncio.to_thread(opened.wait, 2)
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError): await operation
        finish.set()
        self.assertTrue(await asyncio.to_thread(closed.wait, 2))
        await asyncio.sleep(0.01)
        self.assertEqual(scope["relay_slots"]._value,16)


    def test_admin_mutations_use_csrf_and_auth(self):
        tree=ast.parse((ROOT / "Backend/fastapi/routes/external_addon_routes.py").read_text(encoding="utf-8"))
        for name in ("list_addons","save_addon","change_addon"):
            node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
            self.assertTrue(any(isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=="Depends" and d.args[0].id=="require_auth" for d in node.args.defaults))
            if name != "list_addons": self.assertIn("check_csrf",ast.unparse(node))
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="relay")
        self.assertIn("Depends(verify_token)",ast.unparse(node))
        self.assertIn("row['owner']",ast.unparse(node))

if __name__ == "__main__": unittest.main()
