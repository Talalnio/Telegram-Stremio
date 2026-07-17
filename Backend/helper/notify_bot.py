import asyncio
from datetime import datetime, timedelta
from html import escape
import time
from typing import Dict, List, Tuple

import httpx
from pyrogram import Client
from pyrogram.enums import ParseMode
from themoviedb import aioTMDb

from Backend import db
from Backend.config import Telegram
from Backend.logger import LOGGER


tmdb_ar = aioTMDb(key=Telegram.TMDB_API, language="ar-SA", region="SA")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"
_SEND_LOCK = asyncio.Lock()
_LAST_SEND_AT = 0.0
_SEND_GAP_SECONDS = 3.0
_TV_DEBOUNCE_SECONDS = 1.2
_TV_BUFFER: Dict[int, List[dict]] = {}
_TV_BUFFER_TASKS: Dict[int, asyncio.Task] = {}
_DEDUP_LOCK = asyncio.Lock()
_DEDUP_UNTIL: Dict[str, float] = {}
_DEDUP_TTL_SECONDS = 600.0


def _dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(v))
        except Exception:
            return datetime.utcnow()
    if isinstance(v, str) and v.strip():
        s = v.strip()
        try:
            if s.endswith("Z"):
                s = s[:-1]
            return datetime.fromisoformat(s)
        except Exception:
            return datetime.utcnow()
    return datetime.utcnow()


def _latest_episode(tv_doc) -> Tuple[int, int]:
    best = (0, 0)
    for season in tv_doc.get("seasons", []) or []:
        s = int(season.get("season_number") or 0)
        for ep in season.get("episodes", []) or []:
            e = int(ep.get("episode_number") or 0)
            if (s, e) > best:
                best = (s, e)
    return best


async def _get_ar_overview(media_type: str, tmdb_id: int) -> str:
    if not Telegram.TMDB_API or not Telegram.NOTIFY_AR_OVERVIEW:
        return ""
    try:
        if media_type == "movie":
            ar = await tmdb_ar.movie(tmdb_id).details()
        else:
            ar = await tmdb_ar.tv(tmdb_id).details()
        return (getattr(ar, "overview", None) or "").strip()
    except Exception:
        return ""


async def _tmdb_get(path: str, params: dict) -> dict:
    if not Telegram.TMDB_API:
        return {}
    params = dict(params or {})
    params["api_key"] = Telegram.TMDB_API
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.get(f"{TMDB_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _first(items: List[str]) -> str:
    for x in items or []:
        t = (x or "").strip()
        if t:
            return t
    return ""


def _join(items: List[str], max_items: int = 8) -> str:
    out = []
    for x in items or []:
        t = (x or "").strip()
        if not t:
            continue
        out.append(t)
        if len(out) >= max_items:
            break
    return ", ".join(out)


def _as_list(v) -> List:
    if isinstance(v, list):
        return v
    return []


def _pick_poster_url(doc: dict, tmdb_data: dict) -> str:
    p = (doc or {}).get("poster") or ""
    if p and isinstance(p, str):
        return p.strip()
    poster_path = (tmdb_data or {}).get("poster_path") or ""
    if poster_path:
        return f"{TMDB_IMG}{poster_path}"
    return ""


async def _tmdb_enriched(media_type: str, tmdb_id: int) -> dict:
    if media_type == "movie":
        data = await _tmdb_get(f"/movie/{tmdb_id}", {"language": "en-US", "append_to_response": "credits"})
    else:
        data = await _tmdb_get(f"/tv/{tmdb_id}", {"language": "en-US", "append_to_response": "credits"})

    genres = [g.get("name") for g in _as_list(data.get("genres")) if g.get("name")]
    vote_average = float(data.get("vote_average") or 0.0)
    vote_count = int(data.get("vote_count") or 0)

    credits = data.get("credits") or {}
    cast = [c.get("name") for c in _as_list(credits.get("cast")) if c.get("name")]

    if media_type == "movie":
        crew = _as_list(credits.get("crew"))
        directors = [c.get("name") for c in crew if c.get("job") == "Director" and c.get("name")]
        return {
            "genres": genres,
            "vote_average": vote_average,
            "vote_count": vote_count,
            "director": _join(directors, max_items=3),
            "creator": "",
            "stars": _join(cast, max_items=8),
            "poster_url": _pick_poster_url({}, data),
        }

    creators = [c.get("name") for c in _as_list(data.get("created_by")) if c.get("name")]
    return {
        "genres": genres,
        "vote_average": vote_average,
        "vote_count": vote_count,
        "director": "",
        "creator": _join(creators, max_items=4),
        "stars": _join(cast, max_items=8),
        "poster_url": _pick_poster_url({}, data),
    }


async def _touch_notify_state_movie(tmdb_id: int, ts: datetime):
    tracking = db.dbs.get("tracking")
    if tracking is None:
        return
    state = await tracking["state"].find_one({"_id": "notify_state"}) or {}
    last_ts = _dt(state.get("last_ts")) or (datetime.utcnow() - timedelta(days=7))
    seen_movies = set(state.get("seen_movies") or [])
    tv_last: Dict[str, str] = state.get("tv_last") or {}
    if ts > last_ts:
        last_ts = ts
    seen_movies.add(int(tmdb_id))
    await tracking["state"].update_one(
        {"_id": "notify_state"},
        {"$set": {"last_ts": last_ts, "seen_movies": list(seen_movies), "tv_last": tv_last}},
        upsert=True,
    )


async def _touch_notify_state_tv(tmdb_id: int, key: str, ts: datetime):
    tracking = db.dbs.get("tracking")
    if tracking is None:
        return
    state = await tracking["state"].find_one({"_id": "notify_state"}) or {}
    last_ts = _dt(state.get("last_ts")) or (datetime.utcnow() - timedelta(days=7))
    seen_movies = set(state.get("seen_movies") or [])
    tv_last: Dict[str, str] = state.get("tv_last") or {}
    if ts > last_ts:
        last_ts = ts
    tv_last[str(int(tmdb_id))] = (key or "").strip()
    await tracking["state"].update_one(
        {"_id": "notify_state"},
        {"$set": {"last_ts": last_ts, "seen_movies": list(seen_movies), "tv_last": tv_last}},
        upsert=True,
    )


def _code(v) -> str:
    t = ("" if v is None else str(v)).strip()
    return f"<code>{escape(t)}</code>" if t else ""


def _format_score(vote_avg: float, vote_count: int) -> str:
    if vote_count and vote_count > 0:
        return f"{_code(f'{vote_avg:.3f}')} ~ {_code(vote_count)} صوت"
    return _code(f"{vote_avg:.1f}")


def _format_header(title: str, year: str) -> str:
    y = _code(year) if year else ""
    if y:
        return f"<b>{title}</b> ({y})"
    return f"<b>{title}</b>"


def _build_text_movie(
    title: str,
    year: str,
    quality: str,
    size: str,
    genres: str,
    vote_avg: float,
    vote_count: int,
    director: str,
    stars: str,
    story_ar: str,
) -> str:
    lines = [_format_header(title, year)]
    lines.append("")
    if quality:
        lines.append(f"الجودة : {_code(quality)}")
    if size:
        lines.append(f"الحجم : {_code(size)}")
    if genres:
        lines.append("")
        lines.append(f"التصنيفات : {genres}")
    lines.append(f"التقييم ⭐️: {_format_score(vote_avg, vote_count)}")
    if director:
        lines.append(f"المخرج 📽: {director}")
    if stars:
        lines.append(f"الأبطال 👥: {stars}")
    if story_ar:
        lines.append("")
        lines.append("القصة :")
        lines.append(f"<i>“{story_ar}”</i>")
    return "\n".join([x for x in lines if x is not None]).strip()


def _build_text_tv(
    title: str,
    year: str,
    season: int,
    episode: int,
    quality: str,
    size: str,
    genres: str,
    vote_avg: float,
    vote_count: int,
    creator: str,
    stars: str,
    story_ar: str,
) -> str:
    lines = [_format_header(title, year)]
    if season > 0 and episode > 0:
        lines.append(f"الحلقة {_code(episode)} الموسم {_code(season)}")
    lines.append("")
    if quality:
        lines.append(f"الجودة : {_code(quality)}")
    if size:
        lines.append(f"الحجم : {_code(size)}")
    if genres:
        lines.append("")
        lines.append(f"التصنيفات : {genres}")
    lines.append(f"التقييم ⭐️: {_format_score(vote_avg, vote_count)}")
    if creator:
        lines.append(f"المنشئ 📽: {creator}")
    if stars:
        lines.append(f"الأبطال 👥: {stars}")
    if story_ar:
        lines.append("")
        lines.append("القصة :")
        lines.append(f"<i>“{story_ar}”</i>")
    return "\n".join([x for x in lines if x is not None]).strip()


async def _rate_limit_send():
    global _LAST_SEND_AT
    async with _SEND_LOCK:
        now = time.monotonic()
        wait = _SEND_GAP_SECONDS - (now - _LAST_SEND_AT)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_SEND_AT = time.monotonic()


async def _safe_send_message(bot: Client, text: str):
    await _rate_limit_send()
    await bot.send_message(Telegram.NOTIFY_CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def _safe_send_photo(bot: Client, poster_url: str, caption: str):
    await _rate_limit_send()
    await bot.send_photo(Telegram.NOTIFY_CHANNEL_ID, photo=poster_url, caption=caption, parse_mode=ParseMode.HTML)


async def _dedup_allow(key: str) -> bool:
    if not key:
        return True
    now = time.monotonic()
    async with _DEDUP_LOCK:
        expired = [k for k, until in _DEDUP_UNTIL.items() if until <= now]
        for k in expired:
            _DEDUP_UNTIL.pop(k, None)
        until = _DEDUP_UNTIL.get(key)
        if until and until > now:
            return False
        _DEDUP_UNTIL[key] = now + _DEDUP_TTL_SECONDS
        return True


async def _send_notify(bot: Client, poster_url: str, text: str):
    text = (text or "").strip()
    if not text:
        return
    if poster_url:
        try:
            if len(text) <= 950:
                await _safe_send_photo(bot, poster_url, text)
                return
            head = text[:850].rsplit("\n", 1)[0].strip()
            await _safe_send_photo(bot, poster_url, head)
            await _safe_send_message(bot, text)
            return
        except Exception:
            pass
    await _safe_send_message(bot, text)


async def _flush_tv_buffer(bot: Client, tmdb_id: int):
    try:
        await asyncio.sleep(_TV_DEBOUNCE_SECONDS)
        items = _TV_BUFFER.pop(int(tmdb_id), [])
        if not items:
            return
        items.sort(key=lambda x: (int(x.get("season") or 0), int(x.get("episode") or 0)), reverse=True)
        for it in items:
            dedup_key = it.get("dedup_key") or ""
            if dedup_key and not await _dedup_allow(dedup_key):
                continue
            await _send_notify(bot, it.get("poster") or "", it.get("text") or "")
    finally:
        task = _TV_BUFFER_TASKS.pop(int(tmdb_id), None)
        if task and task.cancelled():
            return


async def notify_instant_added(bot: Client, metadata_info: dict, size: str, name: str):
    if not Telegram.NOTIFY_ENABLED or not Telegram.NOTIFY_CHANNEL_ID:
        return
    if not metadata_info:
        return
    try:
        media_type = (metadata_info.get("media_type") or "").lower().strip()
        tmdb_id = int(metadata_info.get("tmdb_id") or 0)
        if not tmdb_id or media_type not in ("movie", "tv"):
            return
        quality_id = (metadata_info.get("encoded_string") or "").strip()

        title = escape((metadata_info.get("title") or "").strip() or ("Movie" if media_type == "movie" else "Series"))
        year = metadata_info.get("year") or metadata_info.get("release_year") or ""

        q = (metadata_info.get("quality") or "").strip() or ""
        size_s = (size or "").strip() or ""

        ar_overview = await _get_ar_overview(media_type, tmdb_id)
        ar_overview = escape(ar_overview) if ar_overview else ""

        enrich = {}
        try:
            enrich = await _tmdb_enriched(media_type, tmdb_id)
        except Exception:
            enrich = {}

        genres = enrich.get("genres") or metadata_info.get("genres") or []
        genres_s = _join([str(g) for g in (genres or [])], max_items=12)
        genres_s = escape(genres_s) if genres_s else ""

        vote_avg = float(enrich.get("vote_average") or metadata_info.get("rate") or 0.0)
        vote_count = int(enrich.get("vote_count") or 0)

        poster = (metadata_info.get("poster") or "").strip()
        if not poster:
            poster = (enrich.get("poster_url") or "").strip()

        stars = escape((enrich.get("stars") or "")[:400])
        director = escape((enrich.get("director") or "")[:200])
        creator = escape((enrich.get("creator") or "")[:200])

        if media_type == "movie":
            text = _build_text_movie(
                title=title,
                year=str(year) if year else "",
                quality=q,
                size=size_s,
                genres=genres_s,
                vote_avg=vote_avg,
                vote_count=vote_count,
                director=director,
                stars=stars,
                story_ar=ar_overview[:900] if ar_overview else "",
            )
            if await _dedup_allow(f"movie:{tmdb_id}:{quality_id}"):
                await _send_notify(bot, poster, text)
        else:
            s = int(metadata_info.get("season_number") or 0)
            e = int(metadata_info.get("episode_number") or 0)
            text = _build_text_tv(
                title=title,
                year=str(year) if year else "",
                season=s,
                episode=e,
                quality=q,
                size=size_s,
                genres=genres_s,
                vote_avg=vote_avg,
                vote_count=vote_count,
                creator=creator,
                stars=stars,
                story_ar=ar_overview[:900] if ar_overview else "",
            )
            tmdb_key = int(tmdb_id)
            _TV_BUFFER.setdefault(tmdb_key, []).append(
                {"season": s, "episode": e, "poster": poster, "text": text, "dedup_key": f"tv:{tmdb_id}:{s:02d}:{e:02d}:{quality_id}"}
            )
            if tmdb_key not in _TV_BUFFER_TASKS or _TV_BUFFER_TASKS[tmdb_key].done():
                _TV_BUFFER_TASKS[tmdb_key] = asyncio.create_task(_flush_tv_buffer(bot, tmdb_key))

        now = datetime.utcnow()
        if media_type == "movie":
            await _touch_notify_state_movie(tmdb_id, now)
        else:
            s = int(metadata_info.get("season_number") or 0)
            e = int(metadata_info.get("episode_number") or 0)
            await _touch_notify_state_tv(tmdb_id, f"{s:02d}x{e:02d}", now)
    except Exception as e:
        LOGGER.error(f"notify_instant_added error: {e}")


async def notify_loop(bot: Client):
    if not Telegram.NOTIFY_ENABLED or not Telegram.NOTIFY_CHANNEL_ID:
        return

    tracking = db.dbs.get("tracking")
    if tracking is None:
        return

    state = await tracking["state"].find_one({"_id": "notify_state"}) or {}
    last_ts = _dt(state.get("last_ts")) or (datetime.utcnow() - timedelta(days=7))
    seen_movies = set(state.get("seen_movies") or [])
    tv_last: Dict[str, str] = state.get("tv_last") or {}

    while True:
        try:
            max_ts = last_ts
            new_seen_movies = set(seen_movies)
            new_tv_last = dict(tv_last)

            for i in range(1, db.current_db_index + 1):
                storage = db.dbs.get(f"storage_{i}")
                if storage is None:
                    continue

                movies = await storage["movie"].find({"updated_on": {"$gt": last_ts}}).sort("updated_on", 1).limit(20).to_list(length=20)
                for m in movies:
                    uts = _dt(m.get("updated_on"))
                    if uts > max_ts:
                        max_ts = uts

                    tmdb_id = int(m.get("tmdb_id") or 0)
                    if not tmdb_id:
                        continue

                    if tmdb_id in new_seen_movies:
                        continue

                    title = escape((m.get("title") or "Movie").strip())
                    year = str(m.get("release_year") or "").strip()
                    overview = await _get_ar_overview("movie", tmdb_id) or (m.get("description") or "")
                    overview = escape((overview or "").strip())[:900]
                    poster = (m.get("poster") or "").strip()

                    telegram = _as_list(m.get("telegram"))
                    q_item = telegram[-1] if telegram else {}
                    q = (q_item.get("quality") or "").strip()
                    size_s = (q_item.get("size") or "").strip()
                    quality_id = (q_item.get("id") or "").strip()

                    enrich = {}
                    try:
                        enrich = await _tmdb_enriched("movie", tmdb_id)
                    except Exception:
                        enrich = {}

                    genres_s = _join([str(g) for g in (enrich.get("genres") or m.get("genres") or [])], max_items=12)
                    genres_s = escape(genres_s) if genres_s else ""
                    vote_avg = float(enrich.get("vote_average") or m.get("rating") or 0.0)
                    vote_count = int(enrich.get("vote_count") or 0)
                    director = escape((enrich.get("director") or "")[:200])
                    stars = escape((enrich.get("stars") or "")[:400]) or escape(_join([str(x) for x in (m.get("cast") or [])], 8))

                    if not poster:
                        poster = (enrich.get("poster_url") or "").strip()

                    text = _build_text_movie(
                        title=title,
                        year=year,
                        quality=q,
                        size=size_s,
                        genres=genres_s,
                        vote_avg=vote_avg,
                        vote_count=vote_count,
                        director=director,
                        stars=stars[:400],
                        story_ar=overview,
                    )
                    if await _dedup_allow(f"movie:{tmdb_id}:{quality_id}"):
                        await _send_notify(bot, poster, text)
                    new_seen_movies.add(tmdb_id)

                tvs = await storage["tv"].find({"updated_on": {"$gt": last_ts}}).sort("updated_on", 1).limit(20).to_list(length=20)
                for tv in tvs:
                    uts = _dt(tv.get("updated_on"))
                    if uts > max_ts:
                        max_ts = uts

                    tmdb_id = int(tv.get("tmdb_id") or 0)
                    if not tmdb_id:
                        continue

                    title = escape((tv.get("title") or "Series").strip())
                    year = str(tv.get("release_year") or "").strip()
                    overview = await _get_ar_overview("tv", tmdb_id) or (tv.get("description") or "")
                    overview = escape((overview or "").strip())[:900]
                    poster = (tv.get("poster") or "").strip()

                    s, e = _latest_episode(tv)
                    key = f"{s:02d}x{e:02d}"
                    prev = new_tv_last.get(str(tmdb_id))

                    should_send = False
                    if prev is None:
                        should_send = True
                    else:
                        try:
                            prev_s, prev_e = (int(x) for x in prev.split("x", 1))
                            should_send = (s, e) > (prev_s, prev_e)
                        except Exception:
                            should_send = key != prev

                    if should_send:
                        q = ""
                        size_s = ""
                        for season in tv.get("seasons", []) or []:
                            if int(season.get("season_number") or 0) != int(s):
                                continue
                            for ep in season.get("episodes", []) or []:
                                if int(ep.get("episode_number") or 0) != int(e):
                                    continue
                                telegram = _as_list(ep.get("telegram"))
                                q_item = telegram[-1] if telegram else {}
                                q = (q_item.get("quality") or "").strip()
                                size_s = (q_item.get("size") or "").strip()
                                quality_id = (q_item.get("id") or "").strip()
                                break
                            break

                        enrich = {}
                        try:
                            enrich = await _tmdb_enriched("tv", tmdb_id)
                        except Exception:
                            enrich = {}

                        genres_s = _join([str(g) for g in (enrich.get("genres") or tv.get("genres") or [])], max_items=12)
                        genres_s = escape(genres_s) if genres_s else ""
                        vote_avg = float(enrich.get("vote_average") or tv.get("rating") or 0.0)
                        vote_count = int(enrich.get("vote_count") or 0)
                        creator = escape((enrich.get("creator") or "")[:200])
                        stars = escape((enrich.get("stars") or "")[:400]) or escape(_join([str(x) for x in (tv.get("cast") or [])], 8))

                        if not poster:
                            poster = (enrich.get("poster_url") or "").strip()

                        text = _build_text_tv(
                            title=title,
                            year=year,
                            season=int(s),
                            episode=int(e),
                            quality=q,
                            size=size_s,
                            genres=genres_s,
                            vote_avg=vote_avg,
                            vote_count=vote_count,
                            creator=creator,
                            stars=stars[:400],
                            story_ar=overview,
                        )
                        if await _dedup_allow(f"tv:{tmdb_id}:{int(s):02d}:{int(e):02d}:{quality_id}"):
                            await _send_notify(bot, poster, text)
                            new_tv_last[str(tmdb_id)] = key

            if max_ts > last_ts:
                last_ts = max_ts

            seen_movies = new_seen_movies
            tv_last = new_tv_last

            await tracking["state"].update_one(
                {"_id": "notify_state"},
                {"$set": {"last_ts": last_ts, "seen_movies": list(seen_movies), "tv_last": tv_last}},
                upsert=True,
            )
        except Exception as e:
            LOGGER.error(f"notify_loop error: {e}")

        await asyncio.sleep(max(10, Telegram.NOTIFY_POLL_SECONDS))
