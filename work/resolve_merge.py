from pathlib import Path
import re
root=Path(r'C:\series\Telegram-Stremio')
pattern=re.compile(r'^<<<<<<< HEAD\n(.*?)^=======\n(.*?)^>>>>>>> upstream/master\n',re.M|re.S)
def resolve(path, choices):
 p=root/path; s=p.read_text(encoding='utf-8'); blocks=list(pattern.finditer(s)); assert len(blocks)==len(choices),(path,len(blocks))
 for m,choice in reversed(list(zip(blocks,choices))):
  a,b=m.groups(); replacement=choice(a,b) if callable(choice) else (a if choice=='prime' else b)
  s=s[:m.start()]+replacement+s[m.end():]
 p.write_text(s,encoding='utf-8',newline='\n')
resolve('Backend/fastapi/routes/api_routes.py',[lambda a,b:b.replace('return {"catalogs": catalogs}', 'return {"catalogs": [{**c, "display_name": catalog_display_name(c)} for c in catalogs]}'),'prime'])
resolve('Backend/fastapi/routes/stremio_routes.py',[
 lambda a,b:a[:a.index('async def _title_allowed')]+b,
 'prime','prime',lambda a,b:b.replace('["tt", "tg", "kitsu"]','["tt", "tg", "kitsu", "xadd:"]'),
 lambda a,b:a.replace('stream_name =','stream_name = sort_name ='),
 'prime',lambda a,b:a+''.join(line+'\n' for line in b.splitlines() if '"trending_' in line)])
resolve('Backend/fastapi/routes/template_routes.py',['prime','prime','prime'])
p=root/'Backend/fastapi/routes/template_routes.py'; s=p.read_text(encoding='utf-8').replace('    _FAILED_LOGINS[ip] = fails + [now]\n','').replace('require_auth, revoke_session, verify_credentials','require_auth, verify_credentials').replace('from Backend.helper.bounded import BoundedDict\n',''); p.write_text(s,encoding='utf-8',newline='\n')
resolve('Backend/fastapi/security/credentials.py',['prime','prime'])
p=root/'Backend/fastapi/security/credentials.py'; s=p.read_text(encoding='utf-8').replace('_revoked_sids = BoundedDict(1000)\n\n\n',''); p.write_text(s,encoding='utf-8',newline='\n')
resolve('Backend/fastapi/templates/custom_catalogs.html',[lambda a,b:b.replace("catalog.name || 'Selected Catalog'", "catalog.display_name || catalog.name || 'Selected Catalog'")])
resolve('Backend/fastapi/templates/media_edit.html',[lambda a,b:'                             onerror="this.onerror=null; this.src=\'/static/placeholder.svg\'">\n'])
resolve('Backend/helper/metadata/providers/tvdb.py',[lambda a,b:a.replace('if not isinstance(item, dict):','if not isinstance(item, dict) or item.get("isAlias"):').replace('name = item.get("name") or name\n                break','name = item.get("name") or name\n                if item.get("isPrimary") and name:\n                    break')])
resolve('README.md',[lambda a,b:a+'\n'+b])
resolve('pyproject.toml',['upstream'])
resolve('uv.lock',['upstream'])
