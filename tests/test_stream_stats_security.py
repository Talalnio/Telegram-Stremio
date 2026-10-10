"""Test real telemetry endpoints and auth dependency without Telegram/database startup."""
import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]

def load_functions(path, names, scope, decorators=False):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if not decorators:
        for n in nodes:
            n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)

class TelemetrySecurityTests(unittest.TestCase):
    def setUp(self):
        async def authenticated(request):
            return request.session.get("admin_sid") == "valid-admin-session"
        self.auth = SimpleNamespace(authenticated=AsyncMock(side_effect=authenticated))
        scope = {"Request": Request, "HTTPException": HTTPException, "HTTP_401_UNAUTHORIZED": 401,
                 "admin_security": self.auth, "urlsplit": urlsplit,
                 "SettingsManager": SimpleNamespace(current=lambda: SimpleNamespace(base_url="http://testserver"))}
        load_functions(ROOT / "Backend/fastapi/security/credentials.py", {"require_auth"}, scope)
        self.secret = "subscriber-secret-token"
        info = {"stream_id": "stream1", "status": "active", "total_bytes": 1024,
                "start_ts": time.time(), "avg_mbps": 2, "meta": {
                    "token": self.secret, "request_path": "/dl/" + self.secret + "/id/movie",
                    "client_host": "private-ip", "title": "Example", "file_name": "Example.mkv"},
                "future_sensitive_field": self.secret}
        self.info = info
        self.recent = deque([dict(info, stream_id="recent1", status="finished")])
        router = APIRouter()
        scope.update({"router": router, "Depends": Depends, "JSONResponse": JSONResponse,
                      "time": time, "deque": deque, "ACTIVE_STREAMS": {"stream1": info},
                      "RECENT_STREAMS": self.recent, "client_dc_map": {}, "work_loads": {}})
        load_functions(ROOT / "Backend/fastapi/routes/stream_routes.py",
                       {"make_json_safe", "_stream_detail_payload", "get_stream_stats", "get_stream_detail"}, scope, True)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-only-secret")
        app.include_router(router)
        @app.get("/test-session/{kind}")
        async def session(request: Request, kind: str):
            request.session.clear()
            request.session["authenticated"] = True
            if kind == "admin":
                request.session["admin_sid"] = "valid-admin-session"
            return {}
        self.client = TestClient(app)

    def test_visitor_subscriber_and_legacy_session_rejected(self):
        for endpoint in ("/stream/stats", "/stream/stats/stream1"):
            for headers in ({}, {"Authorization": "Bearer " + self.secret}):
                self.assertEqual(self.client.get(endpoint, headers=headers).status_code, 401)
        self.client.get("/test-session/legacy")
        self.assertEqual(self.client.get("/stream/stats").status_code, 401)

    def test_admin_dashboard_and_details_no_secrets(self):
        self.client.get("/test-session/admin")
        for endpoint in ("/stream/stats", "/stream/stats/stream1", "/stream/stats/recent1"):
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(self.secret, response.text)
            self.assertNotIn("request_path", response.text)
            self.assertNotIn("private-ip", response.text)
        data = self.client.get("/stream/stats").json()
        self.assertEqual(data["active_streams"][0]["title"], "Example")
        self.assertEqual(data["active_streams"][0]["total_bytes"], 1024)
        self.assertEqual(self.info["meta"]["token"], self.secret)  # usage tracking remains intact

    def test_missing_detail_and_foreign_origin(self):
        self.client.get("/test-session/admin")
        self.assertEqual(self.client.get("/stream/stats/missing").status_code, 404)
        self.assertEqual(self.client.get("/stream/stats", headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.get("/stream/stats", headers={"Origin": "http://testserver"}).status_code, 200)

    def test_revoked_admin_session_rejected(self):
        self.client.get("/test-session/admin")
        self.auth.authenticated.side_effect = None
        self.auth.authenticated.return_value = False
        self.assertEqual(self.client.get("/stream/stats").status_code, 401)
        self.assertEqual(self.client.get("/stream/stats/stream1").status_code, 401)

if __name__ == "__main__":
    unittest.main()
