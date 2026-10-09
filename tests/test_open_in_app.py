"""Exercise the real handler and template without starting Telegram or MongoDB."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]

class OpenPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = Environment(loader=FileSystemLoader(ROOT / "Backend/fastapi/templates"), autoescape=select_autoescape())
        def render(name, context, **kwargs):
            return env.get_template(name).render(**context)
        self.db = SimpleNamespace(get_media_details=AsyncMock(return_value={"backdrop": "https://image.tmdb.org/t/p/original/test.jpg"}))
        tree = ast.parse((ROOT / "Backend/fastapi/main.py").read_text(encoding="utf-8"))
        handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "open_in_app")
        handler.decorator_list = []
        scope = {"asyncio": asyncio, "Request": object, "db": self.db,
                 "templates": SimpleNamespace(TemplateResponse=render), "_abs_media_url": lambda v: v}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "open_handler", "exec"), scope)
        self.open = scope["open_in_app"]

    async def test_nuvio_only_app_button(self):
        page = await self.open(None, "nuvio", "movie", "tt28498219")
        self.assertIn("nuvio://meta?type=movie&amp;id=tt28498219", page)
        self.assertNotIn("Stremio Web", page)
        self.assertNotIn("الخيارين", page)
        self.assertIn('lang="ar" dir="rtl"', page)
        self.assertIn('class="backdrop"', page)

    async def test_stremio_movie_and_series_links(self):
        for media_type, expected in [("movie", "movie"), ("tv", "series"), ("series", "series")]:
            page = await self.open(None, "stremio", media_type, "tt123")
            self.assertIn(f"stremio:///detail/{expected}/tt123", page)
            self.assertIn(f"https://web.stremio.com/#/detail/{expected}/tt123/tt123", page)
            self.assertIn("Stremio Web", page)

    async def test_missing_or_failed_artwork_keeps_links(self):
        for media in [None, {}, {"backdrop": "javascript:alert(1)"}]:
            self.db.get_media_details.return_value = media
            page = await self.open(None, "nuvio", "series", "tt123")
            self.assertNotIn('<img', page)
            self.assertIn("nuvio://meta?type=series", page)
        self.db.get_media_details.side_effect = RuntimeError("offline")
        page = await self.open(None, "stremio", "movie", "tt123")
        self.assertIn("stremio:///detail/movie/tt123", page)

    async def test_untrusted_values_are_escaped(self):
        self.db.get_media_details.return_value = {"backdrop": 'https://example.com/image?x=" onerror="alert(1)'}
        page = await self.open(None, "nuvio", "movie", 'tt123"</script>&x=1')
        self.assertIn("%22%3C%2Fscript%3E%26x%3D1", page)
        self.assertNotIn('x=" onerror="', page)
        self.assertEqual(page.count("</script>"), 1)

if __name__ == "__main__":
    unittest.main()
