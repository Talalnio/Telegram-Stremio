"""Behavioral checks for the Prime/v5.3.2 merge boundaries, without services."""
import ast
import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


def extract(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


class MergeTests(unittest.IsolatedAsyncioTestCase):
    def translations(self):
        return extract("Backend/helper/metadata/providers/tvdb.py", {"_translation_by_lang", "_english_translation"}, {"Optional": __import__('typing').Optional})

    def test_arabic_translation_and_primary_non_alias(self):
        ns = self.translations()
        doc = {"translations": {"nameTranslations": [
            {"language": "ara", "name": "اسم بديل", "isAlias": True},
            {"language": "ara", "name": "عنوان"},
            {"language": "ara", "name": "العنوان الرئيسي", "isPrimary": True},
            {"language": "eng", "name": "English"}],
            "overviewTranslations": [{"language": "eng", "overview": "Fallback"}]}}
        self.assertEqual(ns['_translation_by_lang'](doc, 'ara'), ('العنوان الرئيسي', 'Fallback'))

    def test_english_en_fallback(self):
        ns = self.translations()
        doc = {'translations': {'nameTranslations': [{'language': 'en', 'name': 'English'}]}}
        self.assertEqual(ns['_english_translation'](doc), ('English', None))

    async def test_title_visibility_retains_tmdb_projection(self):
        db = SimpleNamespace(get_media_details=AsyncMock(return_value={'visibility': 'private', 'allowed_tokens': ['owner']}))
        ns = extract('Backend/fastapi/routes/stremio_routes.py', {'_title_allowed'}, {'db': db, '_token_can_view': lambda v, a, t: t['id'] in a})
        self.assertFalse(await ns['_title_allowed'](tmdb_id=123, media_type='movie', token_data={'id':'other'}))
        db.get_media_details.assert_awaited_once_with(imdb_id=None, kitsu_id=None, tmdb_id=123, media_type='movie', projection={'visibility':1, 'allowed_tokens':1})

    def test_global_stream_sort_name_with_custom_formatter_and_cloudflare(self):
        settings = SimpleNamespace(base_url='https://test.example', cf_stream_mode='both')
        ns = extract('Backend/fastapi/routes/stremio_routes.py', {'_streams_from_global_results'}, {
            'format_stream_details':lambda *a, **kw: ('TG', 'details'),
            'parse_size_to_bytes':lambda s: 1024,
            'format_stream':lambda *a, **kw: ('Custom', 'Custom details'),
            'SettingsManager':SimpleNamespace(current=lambda: settings),
            'quote':__import__('urllib.parse',fromlist=['quote']).quote,
            'cf_enabled':lambda:True, 'cf_stream_url':lambda *a:'https://worker.example/video',
            '_binge_group':lambda *a:'group'})
        streams = ns['_streams_from_global_results']('token', [{'title':'video.mkv','quality':'1080p','size':'1KB','source_chat':'test','token':'file'}])
        self.assertEqual(len(streams),2)
        self.assertEqual([s['sort_name'] for s in streams], ['TG🌐 1080p']*2)
        self.assertEqual(streams[0]['name'],'Custom (Cloudflare)')

    async def test_stale_cleanup_releases_load_once(self):
        active={'id':{'last_ts':1,'status':'active','total_bytes':100,'client_index':0,'load_held':True}}
        entry=active['id']; loads={0:2}; recent=deque()
        sleep=AsyncMock(side_effect=[None, asyncio.CancelledError()])
        ns=extract('Backend/helper/custom_dl.py', {'_cleanup_stale_streams'}, {
            'asyncio':SimpleNamespace(sleep=sleep), 'time':SimpleNamespace(time=lambda:1000),
            'ACTIVE_STREAMS':active,'RECENT_STREAMS':recent,'STALE_STREAM_IDLE':180,'work_loads':loads})
        with self.assertRaises(asyncio.CancelledError):await ns['_cleanup_stale_streams']()
        self.assertEqual(loads[0],1)
        self.assertFalse(entry.pop('load_held',False))
        self.assertEqual(recent[0]['status'],'stale')


if __name__ == '__main__':
    unittest.main()
