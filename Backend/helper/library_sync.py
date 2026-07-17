import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import asyncio

from Backend import db
from Backend.config import Telegram
from Backend.helper.pyro import clean_filename


def _safe_fs_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Unknown"
    name = re.sub(r'[<>:"/\\\\|?*]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(". ")
    return name or "Unknown"


def _limit_component(name: str, max_len: int) -> str:
    name = name or ""
    if len(name) <= max_len:
        return name
    h = hashlib.sha1(name.encode("utf-8", errors="ignore")).hexdigest()[:8]
    keep = max(1, max_len - 9)
    return name[:keep].rstrip() + "-" + h


def _resolution_score(q: Optional[dict]) -> int:
    if not q:
        return 0
    text = f"{q.get('quality','')} {q.get('name','')}".lower()
    if "2160" in text or "4k" in text or "uhd" in text:
        return 2160
    if "1080" in text:
        return 1080
    if "720" in text:
        return 720
    if "480" in text:
        return 480
    if "360" in text:
        return 360
    return 1


def _best_source(sources: List[dict]) -> Optional[dict]:
    if not sources:
        return None
    # First sort by resolution score descending, then by part number ascending
    sorted_sources = sorted(
        sources,
        key=lambda x: (-_resolution_score(x), x.get('part_number', 0))
    )
    return sorted_sources[0]


def _build_strm_url(api_token: str, file_id: str) -> str:
    base = (Telegram.BASE_URL or "").rstrip("/")
    return f"{base}/dl/{api_token}/{file_id}/video.mkv"


async def _write_strm_file(path: Path, url: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(url.strip() + "\n")


_DL_RE = re.compile(r"/dl/([^/]+)/([^/]+)/", re.IGNORECASE)


async def _rewrite_existing_strm_urls(root: Path, api_token: str) -> int:
    updated = 0

    for base_dir in (root / "movies", root / "shows"):
        if not base_dir.exists():
            continue

        for p in base_dir.rglob("*.strm"):
            try:
                async with aiofiles.open(p, "r", encoding="utf-8", errors="ignore") as f:
                    first = (await f.readline()).strip()
            except Exception:
                continue

            m = _DL_RE.search(first)
            if not m:
                continue

            file_id = m.group(2)
            new_url = _build_strm_url(api_token, file_id)
            if first == new_url:
                continue

            try:
                await _write_strm_file(p, new_url)
                updated += 1
            except Exception:
                continue

    return updated


def _prune_empty_dirs(root: Path) -> int:
    removed = 0
    for base_dir in (root / "movies", root / "shows"):
        if not base_dir.exists():
            continue
        for p in sorted(base_dir.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if not p.is_dir():
                continue
            try:
                if any(p.iterdir()):
                    continue
                p.rmdir()
                removed += 1
            except Exception:
                continue
    return removed


async def _prune_unexpected_strm(root: Path, expected_paths: set) -> int:
    removed = 0
    for base_dir in (root / "movies", root / "shows"):
        if not base_dir.exists():
            continue

        for p in base_dir.rglob("*.strm"):
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp in expected_paths:
                continue

            try:
                async with aiofiles.open(p, "r", encoding="utf-8", errors="ignore") as f:
                    first = (await f.readline()).strip()
            except Exception:
                continue

            if not _DL_RE.search(first):
                continue

            try:
                p.unlink()
                removed += 1
            except Exception:
                continue

    return removed


async def sync_all(library_path: str, api_token: str) -> Dict[str, Any]:
    root = Path(library_path).resolve()
    root.mkdir(parents=True, exist_ok=True)

    movies_written = 0
    episodes_written = 0
    removed_strm = 0
    removed_dirs = 0
    expected_paths: set = set()

    for db_idx in range(db.current_db_index, 0, -1):
        storage = db.dbs.get(f"storage_{db_idx}")
        if storage is None:
            continue

        async for movie in storage["movie"].find({}):
            title = _safe_fs_name(movie.get("title") or "Movie")
            year = movie.get("release_year") or movie.get("year") or ""
            display = _safe_fs_name(f"{title} ({year})" if year else title)
            display = _limit_component(display, 80)

            telegram_list: List[dict] = movie.get("telegram") or []
            if not telegram_list:
                continue

            best = _best_source(telegram_list)
            if not best:
                continue
            file_id = best.get("id")
            if not file_id:
                continue

            file_name = _limit_component(f"{display}.strm", 120)
            url = _build_strm_url(api_token, file_id)
            out_path = (root / "movies" / display / file_name)
            expected_paths.add(out_path.resolve())
            await _write_strm_file(out_path, url)
            movies_written += 1

        async for tv in storage["tv"].find({}):
            show_title = _safe_fs_name(tv.get("title") or "Series")
            show_year = tv.get("release_year") or tv.get("year") or ""
            show_display = _safe_fs_name(f"{show_title} ({show_year})" if show_year else show_title)
            show_display = _limit_component(show_display, 80)

            seasons: List[dict] = tv.get("seasons") or []
            if not seasons:
                continue

            for season in seasons:
                s_num = int(season.get("season_number") or 0)
                season_folder = f"Season {s_num:02d}"
                episodes: List[dict] = season.get("episodes") or []

                for ep in episodes:
                    e_num = int(ep.get("episode_number") or 0)
                    ep_title = _safe_fs_name(clean_filename(ep.get("title") or f"Episode {e_num}"))

                    telegram_list: List[dict] = ep.get("telegram") or []
                    if not telegram_list:
                        continue

                    best = _best_source(telegram_list)
                    if not best:
                        continue
                    file_id = best.get("id")
                    if not file_id:
                        continue

                    fname = _safe_fs_name(f"{show_display} - S{s_num:02d}E{e_num:02d} - {ep_title}.strm")
                    fname = _limit_component(fname, 140)
                    url = _build_strm_url(api_token, file_id)
                    out_path = (root / "shows" / show_display / season_folder / fname)
                    expected_paths.add(out_path.resolve())
                    await _write_strm_file(out_path, url)
                    episodes_written += 1

    if Telegram.LIBRARY_PRUNE:
        removed_strm = await _prune_unexpected_strm(root, expected_paths)
        if Telegram.LIBRARY_PRUNE_EMPTY_DIRS:
            removed_dirs = _prune_empty_dirs(root)

    existing_updated = 0
    existing_updated = await _rewrite_existing_strm_urls(root, api_token)

    return {
        "status": "success",
        "library_path": str(root),
        "existing_strm_updated": existing_updated,
        "removed_strm": removed_strm,
        "removed_dirs": removed_dirs,
        "movies_written": movies_written,
        "episodes_written": episodes_written,
    }


_SYNC_TASK: Optional[asyncio.Task] = None
_SYNC_LOCK = asyncio.Lock()


async def schedule_sync(trigger: str = ""):
    if not Telegram.AUTO_SYNC_LIBRARY:
        return
    if not Telegram.LIBRARY_PATH or not Telegram.LIBRARY_TOKEN:
        return

    async with _SYNC_LOCK:
        global _SYNC_TASK
        if _SYNC_TASK and not _SYNC_TASK.done():
            return

        async def _run():
            await asyncio.sleep(max(0, int(Telegram.AUTO_SYNC_LIBRARY_DELAY or 0)))
            try:
                await sync_all(Telegram.LIBRARY_PATH, Telegram.LIBRARY_TOKEN)
            except Exception:
                return

        _SYNC_TASK = asyncio.create_task(_run())
