import os
os.environ.update(DATABASE='mongodb://127.0.0.1:27017,mongodb://127.0.0.1:27018',API_ID='12345',API_HASH='0'*32,BOT_TOKEN='12345:test',BASE_URL='http://testserver',SESSION_SECRET='merge-test-only')
from Backend.fastapi.main import app
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
routes=[(r.path,tuple(sorted(r.methods or []))) for r in app.routes if hasattr(r,'methods')]
assert len(routes)==len(set(routes)), 'duplicate routes'
assert any(p.startswith('/webdav') for p,_ in routes)
assert any(p.startswith('/addon-media') for p,_ in routes)
assert any(p=='/api/cf/config' for p,_ in routes)
env=Environment(loader=FileSystemLoader('Backend/fastapi/templates'))
for p in Path('Backend/fastapi/templates').glob('*.html'):env.get_template(p.name)
print(f'App import OK; {len(routes)} route signatures; all Jinja templates parsed; no startup or network calls')
from fastapi.testclient import TestClient
from unittest.mock import patch
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(SessionMiddleware, secret_key="merge-test-only")
client=TestClient(app)
for path in ['/api/admin/settings','/api/admin/stream-analytics','/api/cf/config']:
 response=client.get(path,follow_redirects=False)
 assert response.status_code==401,(path,response.status_code)
with patch('Backend.fastapi.routes.webdav_routes._basic_ok',return_value=False):
 response=client.request('PROPFIND','/webdav/test/')
 assert response.status_code==401,response.status_code
 assert response.headers.get('www-authenticate','').startswith('Basic')
print('HTTP smoke OK: admin API, stream telemetry, CF signature and WebDAV challenge')

