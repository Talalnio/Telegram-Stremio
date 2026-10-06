"""Isolated admin security tests; no Telegram or production database access."""
import ast
import asyncio
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

import mongomock
import pyotp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.testclient import TestClient
from jinja2 import Environment, DictLoader
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]


class AsyncCollection:
    def __init__(self, collection):
        self.collection = collection

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            return getattr(self.collection, name)(*args, **kwargs)
        return call


class Database:
    def __init__(self):
        client = mongomock.MongoClient()
        self.dbs = {"tracking": {name: AsyncCollection(client.tracking[name]) for name in (
            "admin_security", "admin_auth_sessions", "admin_auth_limits"
        )}}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = Database()
        self.settings = types.SimpleNamespace(admin_username="admin", admin_password="strong-test-password",
                                              session_secret="a" * 64, base_url="https://example.test")
        modules = {}
        for name in ("Backend", "Backend.helper", "Backend.fastapi", "Backend.fastapi.security", "Backend.fastapi.routes"):
            module = types.ModuleType(name)
            module.__path__ = [str(ROOT / name.replace(".", "/"))]
            modules[name] = module
        modules["Backend"].db = self.db
        settings = types.ModuleType("Backend.helper.settings_manager")
        settings.SettingsManager = types.SimpleNamespace(current=lambda: self.settings)
        modules[settings.__name__] = settings
        self.original_modules = {k: v for k, v in sys.modules.items() if k == "Backend" or k.startswith("Backend.")}
        sys.modules.update(modules)
        self.module = load("Backend.fastapi.security.two_factor", "Backend/fastapi/security/two_factor.py")
        self.security = self.module.admin_security
        self.credentials = load("Backend.fastapi.security.credentials", "Backend/fastapi/security/credentials.py")
        self.routes = load("Backend.fastapi.routes.security_routes", "Backend/fastapi/routes/security_routes.py")

    def tearDown(self):
        for name in list(sys.modules):
            if name == "Backend" or name.startswith("Backend."):
                del sys.modules[name]
        sys.modules.update(self.original_modules)

    def request(self, session=None):
        return Request({"type": "http", "session": session if session is not None else {},
                        "client": ("127.0.0.1", 1234), "headers": []})

    async def enable(self, request):
        await self.security.start_login(request)
        setup = await self.security.setup(request)
        self.assertIn("svg", setup["qr_svg"])
        codes = await self.security.enable(request, pyotp.TOTP(setup["secret"]).now())
        return setup["secret"], codes["recovery_codes"]

    async def test_enrollment_encryption_and_session_revocation(self):
        first, other = self.request(), self.request()
        await self.security.start_login(first)
        await self.security.start_login(other)
        old_cookie = dict(first.session)
        secret, codes = await self.enable(first)
        state = await self.security.state()
        self.assertTrue(await self.security.authenticated(first))
        self.assertFalse(await self.security.authenticated(other))
        self.assertFalse(await self.security.authenticated(self.request(old_cookie)))
        self.assertNotEqual(state["secret"], secret)
        self.assertNotIn(secret, str(state))
        self.assertEqual(len(state["recovery_hashes"]), 10)
        self.assertNotIn(codes[0].replace("-", ""), str(state))
        self.assertNotIn("secret", str(first.session))

    async def test_password_alone_and_legacy_cookie_cannot_access(self):
        owner = self.request()
        await self.enable(owner)
        login = self.request()
        self.assertTrue(await self.security.start_login(login))
        self.assertFalse(await self.security.authenticated(login))
        with self.assertRaises(HTTPException):
            await self.credentials.require_auth(login)
        legacy = self.request({"authenticated": True, "username": "admin"})
        self.assertFalse(await self.security.authenticated(legacy))

    async def test_factor_replay_and_atomic_recovery(self):
        owner = self.request()
        secret, codes = await self.enable(owner)
        state = await self.security.state()
        self.assertFalse(await self.security.consume_factor(state, pyotp.TOTP(secret).now()))
        results = await asyncio.gather(*(self.security.consume_factor(state, codes[0]) for _ in range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len((await self.security.state())["recovery_hashes"]), 9)
        self.assertFalse(await self.security.consume_factor(state, codes[0]))

    async def test_recovery_login_is_one_use_and_revoked_on_logout(self):
        owner = self.request()
        _, codes = await self.enable(owner)
        login = self.request()
        await self.security.start_login(login)
        challenge = dict(login.session)
        await self.security.finish_login(login, codes[0])
        self.assertTrue(await self.security.authenticated(login))
        with self.assertRaises(HTTPException):
            await self.security.finish_login(self.request(challenge), codes[1])
        cookie = dict(login.session)
        await self.security.revoke(login)
        self.assertFalse(await self.security.authenticated(self.request(cookie)))

    async def test_expired_challenge_and_changed_password(self):
        owner = self.request()
        _, codes = await self.enable(owner)
        login = self.request()
        await self.security.start_login(login)
        await self.security.collection("admin_auth_sessions").update_one(
            {"_id": self.module._hash(login.session["admin_challenge"])},
            {"$set": {"expires_at": datetime.utcnow() - timedelta(seconds=1)}},
        )
        with self.assertRaises(HTTPException):
            await self.security.finish_login(login, codes[0])
        self.settings.admin_password = "changed"
        self.assertFalse(await self.security.authenticated(owner))

    async def test_setup_requires_confirmation_owner_and_expiry(self):
        owner, other = self.request(), self.request()
        await self.security.start_login(owner)
        await self.security.start_login(other)
        data = await self.security.setup(owner)
        self.assertFalse((await self.security.state())["enabled"])
        with self.assertRaises(HTTPException):
            await self.security.enable(other, pyotp.TOTP(data["secret"]).now())
        await self.security.collection("admin_security").update_one(
            {"_id": "admin"}, {"$set": {"pending.expires_at": datetime.utcnow() - timedelta(seconds=1)}}
        )
        with self.assertRaises(HTTPException):
            await self.security.enable(owner, pyotp.TOTP(data["secret"]).now())

    async def test_disable_requires_factor_and_invalidates_other_sessions(self):
        owner = self.request()
        _, codes = await self.enable(owner)
        other = self.request()
        await self.security.start_login(other)
        await self.security.finish_login(other, codes[0])
        with self.assertRaises(HTTPException):
            await self.security.disable(owner, "invalid")
        await self.security.disable(owner, codes[1])
        self.assertFalse((await self.security.state())["enabled"])
        self.assertTrue(await self.security.authenticated(owner))
        self.assertFalse(await self.security.authenticated(other))
        self.assertNotIn("secret", await self.security.state())

    async def test_rate_limits_survive_new_service_instance(self):
        request = self.request()
        for _ in range(10):
            await self.security.throttle(request, "password")
        fresh = self.module.AdminSecurity(self.db)
        with self.assertRaises(HTTPException) as raised:
            await fresh.throttle(request, "password")
        self.assertEqual(raised.exception.status_code, 429)

    async def test_origin_guard_and_expired_session(self):
        request = self.request()
        await self.security.start_login(request)
        for origin, permitted in (("https://example.test", True), ("https://evil.test", False)):
            request = self.request(dict(request.session))
            request.scope["headers"] = [(b"origin", origin.encode())]
            if permitted:
                self.assertTrue(await self.credentials.require_auth(request))
            else:
                with self.assertRaises(HTTPException) as raised:
                    await self.credentials.require_auth(request)
                self.assertEqual(raised.exception.status_code, 403)
        await self.security.collection("admin_auth_sessions").update_one(
            {"_id": self.module._hash(request.session["admin_sid"])},
            {"$set": {"expires_at": datetime.utcnow() - timedelta(seconds=1)}},
        )
        self.assertFalse(await self.security.authenticated(request))

    async def test_atomic_totp_and_unicode_input(self):
        request = self.request()
        await self.security.start_login(request)
        setup = await self.security.setup(request)
        with self.assertRaises(HTTPException):
            await self.security.enable(request, "١٢٣٤٥٦")
        await self.security.enable(request, pyotp.TOTP(setup["secret"]).now())
        state = await self.security.state()
        next_step = state["last_step"] + 1
        with patch("time.time", return_value=next_step * 30):
            code = pyotp.TOTP(setup["secret"]).at(next_step * 30)
            results = await asyncio.gather(*(self.security.consume_factor(state, code) for _ in range(2)))
        self.assertEqual(results.count(True), 1)

    async def test_csrf(self):
        request = self.request()
        token = self.module.csrf_token(request)
        self.module.check_csrf(request, token)
        for invalid in (None, "bad"):
            with self.assertRaises(HTTPException):
                self.module.check_csrf(request, invalid)

    def test_http_login_flow_and_management_guards(self):
        # Extract the actual handlers without starting Telegram at import time.
        source = ast.parse((ROOT / "Backend/fastapi/routes/template_routes.py").read_text(encoding="utf-8"))
        names = {"login_page", "login_post", "login_factor_post", "logout"}
        handlers = [node for node in source.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        env = Environment(loader=DictLoader({
            "base.html": "{% block title %}{% endblock %}{% block content %}{% endblock %}",
            "login.html": (ROOT / "Backend/fastapi/templates/login.html").read_text(encoding="utf-8"),
        }), autoescape=True)
        class Templates:
            def TemplateResponse(self, name, context, status_code=200):
                return HTMLResponse(env.get_template(name).render(context), status_code=status_code)
        ns = dict(vars(self.module), Request=Request, RedirectResponse=RedirectResponse,
                  verify_credentials=self.credentials.verify_credentials, _base_context=lambda request: {}, templates=Templates())
        exec(compile(ast.Module(body=handlers, type_ignores=[]), "handlers", "exec"), ns)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=self.settings.session_secret)
        app.include_router(self.routes.router)
        app.get("/login")(ns["login_page"])
        from fastapi import Form
        @app.post("/login")
        async def login(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
            return await ns["login_post"](request, username, password, csrf)
        @app.post("/login/2fa")
        async def factor(request: Request, code: str = Form(...), csrf: str = Form(...)):
            return await ns["login_factor_post"](request, code, csrf)
        @app.get("/")
        async def home(request: Request):
            await self.credentials.require_auth(request)
            return {"ok": True}
        import re
        with TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 401)
            page = client.get("/login")
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            self.assertEqual(client.post("/login", data={"username":"admin", "password":"strong-test-password", "csrf":"bad"}).status_code, 403)
            self.assertEqual(client.post("/login", data={"username":"admin", "password":"wrong", "csrf":csrf}).status_code, 400)
            self.assertEqual(client.post("/login", data={"username":"admin", "password":"strong-test-password", "csrf":csrf}).status_code, 200)
            status = client.get("/api/admin/security/2fa").json()
            path = "/api/admin/security/2fa/"
            self.assertEqual(client.post(path + "setup", json={"password":"strong-test-password"}).status_code, 403)
            headers = {"X-CSRF-Token":status["csrf"]}
            self.assertEqual(client.post(path + "setup", headers=headers, json={"password":"wrong"}).status_code, 400)
            setup = client.post(path + "setup", headers=headers, json={"password":"strong-test-password"}).json()
            enabled = client.post(path + "enable", headers=headers, json={"password":"strong-test-password", "code":pyotp.TOTP(setup["secret"]).now()})
            self.assertEqual(enabled.status_code, 200)
            codes = enabled.json()["recovery_codes"]
            self.assertEqual(client.get("/api/admin/security/2fa").json()["remaining_codes"], 10)
            client.cookies.clear()
            page = client.get("/login")
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            page = client.post("/login", data={"username":"admin", "password":"strong-test-password", "csrf":csrf})
            self.assertIn('name="code"', page.text)
            self.assertEqual(client.get("/").status_code, 401)
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            self.assertEqual(client.post("/login/2fa", data={"code":"bad", "csrf":csrf}).status_code, 400)
            self.assertEqual(client.post("/login/2fa", data={"code":codes[0], "csrf":csrf}).status_code, 200)
            self.assertEqual(client.get("/api/admin/security/2fa").json()["remaining_codes"], 9)


if __name__ == "__main__":
    unittest.main()
