import re
import time
import difflib
from typing import Dict, List, Optional, Tuple

import httpx
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from Backend import db
from Backend.config import Telegram
from Backend.logger import LOGGER


TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"
_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
_SXXEXX_RE = re.compile(r"\bS(\d{1,2})E(\d{1,2})\b", re.IGNORECASE)

_STOPWORDS = {
    "kdrama",
    "drama",
    "series",
    "season",
    "episode",
    "episodes",
    "movie",
    "film",
    "please",
    "pls",
    "plz",
    "for",
    "anyone",
    "any1",
    "request",
    "req",
    "watch",
    "full",
    "hd",
    "4k",
    "1080p",
    "720p",
    "مسلسل",
    "فيلم",
    "حلقة",
    "حلقه",
    "حلقات",
    "موسم",
    "الموسم",
    "ابي",
    "أبي",
    "ابغى",
    "أبغى",
    "طلب",
    "رجاء",
    "لو",
    "ممكن",
}


def _split_alpha_num(text: str) -> str:
    t = text or ""
    t = re.sub(r"(?<=[A-Za-z\u0600-\u06FF])(?=\d)", " ", t)
    t = re.sub(r"(?<=\d)(?=[A-Za-z\u0600-\u06FF])", " ", t)
    return t


def _normalize_query(text: str, remove_stopwords: bool = True) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\b(?:https?|ftp)://\S+\b", " ", t, flags=re.IGNORECASE)
    t = t.replace("_", " ").replace("-", " ").replace(".", " ")
    t = _split_alpha_num(t)
    t = re.sub(r"[^\w\u0600-\u06FF]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip().lower()
    if not t:
        return ""
    tokens = [x for x in t.split(" ") if x]
    if remove_stopwords:
        kept = [x for x in tokens if x not in _STOPWORDS]
        if kept:
            tokens = kept
    return " ".join(tokens).strip()


def _similarity(a: str, b: str) -> float:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _required_channel_id() -> int:
    return int(Telegram.REQUIRED_CHANNEL_ID or Telegram.NOTIFY_CHANNEL_ID or 0)


def _required_invite_link() -> str:
    return (Telegram.REQUIRED_INVITE_LINK or Telegram.NOTIFY_INVITE_LINK or Telegram.REQUESTS_INVITE_LINK or "").strip()


def _truncate(text: str, max_len: int) -> str:
    t = (text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)].rstrip() + "…"


def _parse_user_query(text: str) -> Dict:
    raw = (text or "").strip()
    raw = re.sub(r"\s+", " ", raw).strip()
    year = None
    m = _YEAR_RE.search(raw)
    if m:
        try:
            year_i = int(m.group(1))
            if 1900 <= year_i <= 2099:
                year = year_i
                raw = raw.replace(m.group(1), " ")
        except Exception:
            year = None

    s = e = None
    me = _SXXEXX_RE.search(raw)
    if me:
        s = int(me.group(1))
        e = int(me.group(2))
        raw = _SXXEXX_RE.sub("", raw).strip()

    media_hint = None
    lowered = raw.lower()
    if "مسلسل" in raw or "series" in lowered or "tv" in lowered:
        media_hint = "tv"
        raw = raw.replace("مسلسل", "").replace("Tv", "").replace("tv", "").replace("Series", "").replace("series", "").strip()
    if "فيلم" in raw or "movie" in lowered or "film" in lowered:
        media_hint = "movie"
        raw = raw.replace("فيلم", "").replace("Movie", "").replace("movie", "").replace("Film", "").replace("film", "").strip()

    q = _normalize_query(raw, remove_stopwords=False)
    return {"query": q, "year": year, "season": s, "episode": e, "media_hint": media_hint}


async def _is_allowed(client: Client, user_id: int) -> bool:
    required = _required_channel_id()
    if not required:
        return True
    try:
        member = await client.get_chat_member(required, user_id)
        return member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


def _invite_keyboard() -> InlineKeyboardMarkup:
    link = _required_invite_link()
    buttons = []
    if link:
        buttons.append([InlineKeyboardButton("🔗 قناة الإشعارات", url=link)])
    buttons.append([InlineKeyboardButton("🔄 تحقق مرة ثانية", callback_data="rq|check")])
    return InlineKeyboardMarkup(buttons)


def _type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 فيلم", callback_data="rq|type|movie"),
                InlineKeyboardButton("📺 مسلسل", callback_data="rq|type|tv"),
            ],
            [InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")],
        ]
    )


async def _tmdb_get(path: str, params: dict) -> dict:
    if not Telegram.TMDB_API:
        return {}
    params = dict(params or {})
    params["api_key"] = Telegram.TMDB_API
    async with httpx.AsyncClient(timeout=20) as h:
        r = await h.get(f"{TMDB_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()


def _pick_year(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        return date_str.split("-", 1)[0]
    except Exception:
        return ""


async def _tmdb_search(media_type: str, query: str, year: Optional[int]) -> List[dict]:
    base = _normalize_query(query, remove_stopwords=True)
    alt = _normalize_query(query, remove_stopwords=False)
    if not base and not alt:
        return []

    max_results = max(1, Telegram.REQUESTS_MAX_RESULTS)
    pool_target = max(10, max_results * 5)

    variants: List[str] = []
    if base:
        variants.append(base)
    if alt and alt != base:
        variants.append(alt)

    base_tokens = base.split() if base else []
    if len(base_tokens) >= 2:
        longest = max(base_tokens, key=len)
        if longest and longest not in variants:
            variants.append(longest)

    variants = variants[:3]

    seen = set()
    pooled: List[dict] = []

    async def _search_once(q: str, language: str):
        params = {"query": q, "include_adult": "false", "language": language, "page": 1}
        if media_type == "movie" and year:
            params["year"] = int(year)
        if media_type == "tv" and year:
            params["first_air_date_year"] = int(year)
        data = await _tmdb_get(f"/search/{'movie' if media_type=='movie' else 'tv'}", params)
        return data.get("results") or []

    for idx, qv in enumerate(variants):
        try:
            en = await _search_once(qv, "en-US")
            ar = await _search_once(qv, "ar-SA")
        except Exception:
            if idx == 0:
                raise
            continue

        for lst in (en, ar):
            for it in lst:
                tid = it.get("id")
                if not tid:
                    continue
                if tid in seen:
                    continue
                seen.add(tid)
                title = it.get("title") if media_type == "movie" else it.get("name")
                date = it.get("release_date") if media_type == "movie" else it.get("first_air_date")
                pooled.append({"id": int(tid), "title": (title or "").strip(), "year": _pick_year(date)})
                if len(pooled) >= pool_target:
                    break
            if len(pooled) >= pool_target:
                break
        if len(pooled) >= max_results and idx == 0:
            break
        if pooled and idx == 0 and len(pooled) >= max_results:
            break

    if not pooled:
        return []

    q_for_score = base or alt or ""
    scored = []
    for it in pooled:
        title = it.get("title") or ""
        title_norm = _normalize_query(title, remove_stopwords=False)
        score = _similarity(q_for_score, title_norm)
        if year and it.get("year") and str(year) == str(it.get("year")):
            score += 0.15
        scored.append((score, it))

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = []
    used = set()
    for _, it in scored:
        tid = it["id"]
        if tid in used:
            continue
        used.add(tid)
        picked.append(it)
        if len(picked) >= max_results:
            break
    return picked


async def _tmdb_details(media_type: str, tmdb_id: int) -> dict:
    en = await _tmdb_get(f"/{'movie' if media_type=='movie' else 'tv'}/{tmdb_id}", {"language": "en-US"})
    ar = await _tmdb_get(f"/{'movie' if media_type=='movie' else 'tv'}/{tmdb_id}", {"language": "ar-SA"})

    title_en = (en.get("title") if media_type == "movie" else en.get("name")) or ""
    date = (en.get("release_date") if media_type == "movie" else en.get("first_air_date")) or ""
    year = _pick_year(date)
    rating = float(en.get("vote_average") or 0.0)
    genres = ", ".join([g.get("name") for g in (en.get("genres") or []) if g.get("name")])[:120]
    overview = (ar.get("overview") or "").strip() or (en.get("overview") or "").strip()
    poster_path = en.get("poster_path") or ""
    poster_url = f"{TMDB_IMG}{poster_path}" if poster_path else ""
    seasons_count = int(en.get("number_of_seasons") or 0) if media_type == "tv" else 0

    return {
        "tmdb_id": int(tmdb_id),
        "media_type": media_type,
        "title_en": title_en.strip() or ("Movie" if media_type == "movie" else "Series"),
        "year": year,
        "rating": rating,
        "genres": genres,
        "overview": overview,
        "poster_url": poster_url,
        "seasons_count": seasons_count,
    }


async def _tmdb_season_episodes(tv_id: int, season_number: int) -> List[dict]:
    data = await _tmdb_get(f"/tv/{tv_id}/season/{season_number}", {"language": "en-US"})
    eps = []
    for ep in (data.get("episodes") or []):
        eps.append({"episode_number": int(ep.get("episode_number") or 0), "name": (ep.get("name") or "").strip()})
    return [e for e in eps if e["episode_number"] > 0]


def _card_text(details: dict) -> str:
    title = details.get("title_en") or ""
    year = details.get("year") or ""
    rating = details.get("rating") or 0.0
    genres = details.get("genres") or ""
    overview = details.get("overview") or ""
    header = f"<b>{title}</b>"
    if year:
        header += f" ({year})"
    line = f"{header}\n⭐ {rating:.1f}"
    if genres:
        line += f"\n🎭 {genres}"
    if overview:
        line += f"\n\n<i>“{_truncate(overview, 700)}”</i>"
    return line


def _admin_keyboard(request_id: int) -> InlineKeyboardMarkup:
    base = f"rq|adm|{request_id}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ قبول", callback_data=base + "|ok"),
                InlineKeyboardButton("❌ رفض", callback_data=base + "|no"),
                InlineKeyboardButton("📌 تم الإضافة", callback_data=base + "|added"),
            ]
        ]
    )


async def _tracking_db():
    t = db.dbs.get("tracking")
    if t is None:
        raise RuntimeError("Tracking DB not available")
    return t


async def _session_get(user_id: int) -> dict:
    t = await _tracking_db()
    doc = await t["req_sessions"].find_one({"_id": int(user_id)}) or {}
    return doc.get("data") or {}


async def _session_set(user_id: int, data: dict):
    t = await _tracking_db()
    await t["req_sessions"].update_one(
        {"_id": int(user_id)},
        {"$set": {"data": data, "updated_at": time.time()}},
        upsert=True,
    )


async def _session_clear(user_id: int):
    t = await _tracking_db()
    await t["req_sessions"].delete_one({"_id": int(user_id)})


async def _send_or_edit(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None):
    if message:
        try:
            return await message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception:
            pass
    return None


async def _edit_message_body(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None):
    if not message:
        return None
    try:
        if (message.photo or message.video or message.document or message.animation or message.audio) and message.caption is not None:
            return await message.edit_caption(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        return None


@Client.on_message(filters.private & filters.text & ~filters.service, group=9)
async def requests_text_router(client: Client, message: Message):
    if not Telegram.REQUESTS_ENABLED:
        return
    if not message.from_user:
        return
    if (message.text or "").startswith("/"):
        return

    uid = message.from_user.id
    if not await _is_allowed(client, uid):
        await message.reply_text("لازم تكون مشترك في قناة الإشعارات أولاً.", reply_markup=_invite_keyboard())
        return

    parsed = _parse_user_query(message.text or "")
    if not parsed["query"]:
        await message.reply_text("اكتب اسم الفيلم/المسلسل.", reply_markup=_type_keyboard())
        return

    data = {"query": parsed["query"], "year": parsed["year"], "season": parsed["season"], "episode": parsed["episode"], "media_type": parsed["media_hint"], "step": "choose_type"}
    await _session_set(uid, data)

    if parsed["media_hint"] in ("movie", "tv"):
        loading = await message.reply_text(
            "🔎 جاري تجهيز النتائج...",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏳", callback_data="rq|noop")]]),
        )
        await _handle_type_choice(client, loading, uid, parsed["media_hint"])
        return

    await message.reply_text("حدد النوع:", reply_markup=_type_keyboard())


@Client.on_message(filters.command(["request", "طلب"]) & filters.private, group=10)
async def request_command(client: Client, message: Message):
    if not Telegram.REQUESTS_ENABLED:
        return
    if not message.from_user:
        return
    uid = message.from_user.id
    if not await _is_allowed(client, uid):
        await message.reply_text("لازم تكون مشترك في قناة الإشعارات أولاً.", reply_markup=_invite_keyboard())
        return
    parts = (message.text or "").split(maxsplit=1)
    q = parts[1].strip() if len(parts) > 1 else ""
    if not q:
        await message.reply_text("اكتب اسم الفيلم/المسلسل بعد الأمر.\nمثال: /request Interstellar")
        return
    parsed = _parse_user_query(q)
    data = {"query": parsed["query"], "year": parsed["year"], "season": parsed["season"], "episode": parsed["episode"], "media_type": parsed["media_hint"], "step": "choose_type"}
    await _session_set(uid, data)
    if parsed["media_hint"] in ("movie", "tv"):
        await message.reply_text("حدد النوع:", reply_markup=_type_keyboard())
    else:
        await message.reply_text("حدد النوع:", reply_markup=_type_keyboard())


@Client.on_callback_query(filters.regex(r"^rq\|check$"))
async def rq_check(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    ok = await _is_allowed(client, cq.from_user.id)
    if ok:
        await cq.message.edit_text("✅ تم التحقق. اكتب اسم الفيلم/المسلسل هنا.")
    else:
        await cq.answer("لسه مو عضو/تم قبولك.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^rq\|cancel$"))
async def rq_cancel(client: Client, cq: CallbackQuery):
    if cq.from_user:
        await _session_clear(cq.from_user.id)
    try:
        await cq.message.edit_text("تم الإلغاء.")
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^rq\|type\|(movie|tv)$"))
async def rq_type(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    media_type = (cq.data or "").split("|", 2)[2]
    await _handle_type_choice(client, cq.message, cq.from_user.id, media_type, cq=cq)


async def _handle_type_choice(client: Client, message: Message, user_id: int, media_type: str, cq: CallbackQuery = None):
    sess = await _session_get(user_id)
    sess["media_type"] = media_type
    sess["step"] = "results"
    await _session_set(user_id, sess)

    query = sess.get("query") or ""
    year = sess.get("year")

    try:
        results = await _tmdb_search(media_type, query, year)
    except Exception as e:
        LOGGER.error(f"tmdb search error: {e}")
        if cq:
            await cq.answer("تعذر البحث الآن.", show_alert=True)
        return

    if not results:
        await _send_or_edit(
            message,
            "ما حصلت نتائج. جرّب تكتب الاسم بشكل أبسط أو مع السنة.\nمثال: Breaking Bad 2008",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔁 إعادة البحث", callback_data="rq|restart")],
                    [InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")],
                ]
            ),
        )
        return

    rows = []
    prefix = "🎬" if media_type == "movie" else "📺"
    kind_word = "فيلم" if media_type == "movie" else "مسلسل"
    for it in results:
        label = it["title"] or query
        y = it["year"]
        text = f"⭐ {prefix} {kind_word} | {label} ({y})" if y else f"⭐ {prefix} {kind_word} | {label}"
        rows.append([InlineKeyboardButton(_truncate(text, 60), callback_data=f"rq|sel|{media_type}|{it['id']}")])

    rows.append([InlineKeyboardButton("🔎 لم يظهر العمل؟", callback_data="rq|refine")])
    rows.append([InlineKeyboardButton("🔁 إعادة البحث", callback_data="rq|restart")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")])

    await _send_or_edit(message, "اختر النتيجة:", reply_markup=InlineKeyboardMarkup(rows))


@Client.on_callback_query(filters.regex(r"^rq\|refine$"))
async def rq_refine(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    sess = await _session_get(cq.from_user.id)
    sess["step"] = "need_year"
    await _session_set(cq.from_user.id, sess)
    await cq.message.edit_text("اذكر سنة العمل (مثال: 2022) أو ارسل الاسم مع السنة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")]]))


@Client.on_callback_query(filters.regex(r"^rq\|restart$"))
async def rq_restart(client: Client, cq: CallbackQuery):
    if cq.from_user:
        await _session_clear(cq.from_user.id)
    await _edit_message_body(
        cq.message,
        "اكتب اسم الفيلم/المسلسل من جديد.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")]]),
    )


@Client.on_message(filters.private & filters.text & ~filters.service, group=11)
async def rq_year_input(client: Client, message: Message):
    if not Telegram.REQUESTS_ENABLED:
        return
    if not message.from_user:
        return
    if (message.text or "").startswith("/"):
        return

    uid = message.from_user.id
    sess = await _session_get(uid)
    if sess.get("step") != "need_year":
        return

    parsed = _parse_user_query(message.text or "")
    if parsed["year"]:
        sess["year"] = parsed["year"]
        sess["step"] = "results"
        await _session_set(uid, sess)
        await message.reply_text("تمام. اختر النوع:", reply_markup=_type_keyboard())
        return
    await message.reply_text("اكتب السنة فقط مثل: 2022", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")]]))


@Client.on_callback_query(filters.regex(r"^rq\|sel\|(movie|tv)\|(\d+)$"))
async def rq_select(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    _, _, media_type, sid = (cq.data or "").split("|", 3)
    tmdb_id = int(sid)
    uid = cq.from_user.id

    try:
        details = await _tmdb_details(media_type, tmdb_id)
    except Exception as e:
        LOGGER.error(f"tmdb details error: {e}")
        await cq.answer("تعذر جلب التفاصيل.", show_alert=True)
        return

    sess = await _session_get(uid)
    sess.update({"media_type": media_type, "tmdb_id": tmdb_id, "step": "details"})
    await _session_set(uid, sess)

    try:
        await cq.message.delete()
    except Exception:
        pass

    text = _card_text(details)

    if media_type == "movie":
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📨 إرسال الطلب", callback_data="rq|send|movie"),
                    InlineKeyboardButton("🔁 إعادة البحث", callback_data="rq|restart"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel"),
                ]
            ]
        )
    else:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📦 جميع المواسم", callback_data="rq|send|tv|all"),
                    InlineKeyboardButton("🎯 موسم محدد", callback_data="rq|tv|seasons"),
                ],
                [
                    InlineKeyboardButton("🔁 إعادة البحث", callback_data="rq|restart"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel"),
                ],
            ]
        )

    poster = details.get("poster_url")
    if poster:
        await client.send_photo(uid, photo=poster, caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await client.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^rq\|tv\|seasons$"))
async def rq_tv_seasons(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    sess = await _session_get(cq.from_user.id)
    tmdb_id = int(sess.get("tmdb_id") or 0)
    if not tmdb_id:
        await cq.answer("انتهت الجلسة.", show_alert=True)
        return
    try:
        details = await _tmdb_details("tv", tmdb_id)
    except Exception:
        await cq.answer("تعذر جلب المواسم.", show_alert=True)
        return
    count = int(details.get("seasons_count") or 0)
    if count <= 0:
        await cq.answer("لا توجد مواسم.", show_alert=True)
        return
    rows = []
    for s in range(1, min(count, 25) + 1):
        rows.append([InlineKeyboardButton(f"Season {s:02d}", callback_data=f"rq|tv|season|{s}")])
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="rq|back|details")])
    await cq.message.edit_reply_markup(InlineKeyboardMarkup(rows))


@Client.on_callback_query(filters.regex(r"^rq\|back\|details$"))
async def rq_back_details(client: Client, cq: CallbackQuery):
    await cq.answer("استخدم الأزرار.", show_alert=False)


@Client.on_callback_query(filters.regex(r"^rq\|tv\|season\|(\d+)$"))
async def rq_tv_season_pick(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    season_number = int((cq.data or "").split("|", 3)[3])
    sess = await _session_get(cq.from_user.id)
    tv_id = int(sess.get("tmdb_id") or 0)
    if not tv_id:
        await cq.answer("انتهت الجلسة.", show_alert=True)
        return
    sess["season"] = season_number
    sess["step"] = "episodes"
    await _session_set(cq.from_user.id, sess)
    await _render_episodes(client, cq, tv_id, season_number, page=1)


async def _render_episodes(client: Client, cq: CallbackQuery, tv_id: int, season_number: int, page: int):
    eps = await _tmdb_season_episodes(tv_id, season_number)
    if not eps:
        await cq.answer("لا توجد حلقات.", show_alert=True)
        return

    per = 10
    total_pages = max(1, (len(eps) + per - 1) // per)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per
    chunk = eps[start : start + per]

    rows = []
    for ep in chunk:
        e = ep["episode_number"]
        name = ep["name"] or f"Episode {e}"
        rows.append([InlineKeyboardButton(f"E{e:02d} - {_truncate(name, 34)}", callback_data=f"rq|tv|ep|{season_number}|{e}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"rq|tv|eps|{season_number}|{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="rq|noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"rq|tv|eps|{season_number}|{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("📦 طلب كل حلقات الموسم", callback_data=f"rq|send|tv|season|{season_number}")])
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="rq|tv|seasons")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")])

    await cq.message.edit_reply_markup(InlineKeyboardMarkup(rows))


@Client.on_callback_query(filters.regex(r"^rq\|tv\|eps\|(\d+)\|(\d+)$"))
async def rq_tv_eps_page(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    season_number = int((cq.data or "").split("|", 4)[3])
    page = int((cq.data or "").split("|", 4)[4])
    sess = await _session_get(cq.from_user.id)
    tv_id = int(sess.get("tmdb_id") or 0)
    if not tv_id:
        await cq.answer("انتهت الجلسة.", show_alert=True)
        return
    await _render_episodes(client, cq, tv_id, season_number, page)


@Client.on_callback_query(filters.regex(r"^rq\|tv\|ep\|(\d+)\|(\d+)$"))
async def rq_tv_ep_pick(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    _, _, _, s, e = (cq.data or "").split("|", 4)
    season_number = int(s)
    episode_number = int(e)
    sess = await _session_get(cq.from_user.id)
    sess["season"] = season_number
    sess["episode"] = episode_number
    await _session_set(cq.from_user.id, sess)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📨 إرسال الطلب", callback_data="rq|send|tv|episode"),
                InlineKeyboardButton("➕ طلب حلقة أخرى", callback_data=f"rq|tv|season|{season_number}"),
            ],
            [InlineKeyboardButton("❌ إلغاء", callback_data="rq|cancel")],
        ]
    )
    await cq.answer("تم اختيار الحلقة.", show_alert=False)
    await cq.message.edit_reply_markup(kb)


@Client.on_callback_query(filters.regex(r"^rq\|send\|movie$"))
async def rq_send_movie(client: Client, cq: CallbackQuery):
    await _send_request_to_admin(client, cq, kind="movie")


@Client.on_callback_query(filters.regex(r"^rq\|send\|tv\|all$"))
async def rq_send_tv_all(client: Client, cq: CallbackQuery):
    await _send_request_to_admin(client, cq, kind="tv_all")


@Client.on_callback_query(filters.regex(r"^rq\|send\|tv\|season\|(\d+)$"))
async def rq_send_tv_season(client: Client, cq: CallbackQuery):
    season_number = int((cq.data or "").split("|", 4)[4])
    await _send_request_to_admin(client, cq, kind="tv_season", season=season_number)


@Client.on_callback_query(filters.regex(r"^rq\|send\|tv\|episode$"))
async def rq_send_tv_episode(client: Client, cq: CallbackQuery):
    await _send_request_to_admin(client, cq, kind="tv_episode")


async def _send_request_to_admin(client: Client, cq: CallbackQuery, kind: str, season: Optional[int] = None):
    if not cq.from_user:
        return
    uid = cq.from_user.id

    if not Telegram.REQUESTS_CHANNEL_ID:
        await cq.answer("قناة الإدارة غير مضبوطة.", show_alert=True)
        return

    if not await _is_allowed(client, uid):
        await cq.answer("لازم تكون مشترك في قناة الإشعارات.", show_alert=True)
        return

    sess = await _session_get(uid)
    media_type = sess.get("media_type")
    tmdb_id = int(sess.get("tmdb_id") or 0)
    if not tmdb_id or media_type not in ("movie", "tv"):
        await cq.answer("انتهت الجلسة.", show_alert=True)
        return

    details = await _tmdb_details(media_type, tmdb_id)
    caption = _card_text(details)

    req_payload = {"user_id": int(uid), "tmdb_id": int(tmdb_id), "media_type": media_type, "kind": kind}
    if kind == "tv_season":
        req_payload["season"] = int(season or sess.get("season") or 0)
    if kind == "tv_episode":
        req_payload["season"] = int(sess.get("season") or 0)
        req_payload["episode"] = int(sess.get("episode") or 0)

    suffix = ""
    if kind == "tv_all":
        suffix = "\n\n<b>الطلب:</b> جميع المواسم"
    elif kind == "tv_season":
        suffix = f"\n\n<b>الطلب:</b> Season {int(req_payload.get('season') or 0):02d}"
    elif kind == "tv_episode":
        suffix = f"\n\n<b>الطلب:</b> S{int(req_payload.get('season') or 0):02d}E{int(req_payload.get('episode') or 0):02d}"
    else:
        suffix = "\n\n<b>الطلب:</b> فيلم"

    caption_admin = caption + suffix + f"\n\nTMDb: https://www.themoviedb.org/{'movie' if media_type=='movie' else 'tv'}/{tmdb_id}"

    t = await _tracking_db()
    rid = int(time.time() * 1000) + (uid % 1000)
    await t["requests"].update_one({"_id": rid}, {"$set": req_payload}, upsert=True)

    poster = details.get("poster_url")
    if poster:
        await client.send_photo(Telegram.REQUESTS_CHANNEL_ID, photo=poster, caption=caption_admin, parse_mode=ParseMode.HTML, reply_markup=_admin_keyboard(rid))
    else:
        await client.send_message(Telegram.REQUESTS_CHANNEL_ID, caption_admin, parse_mode=ParseMode.HTML, reply_markup=_admin_keyboard(rid), disable_web_page_preview=True)

    await cq.message.edit_caption(
        (cq.message.caption or "") + "\n\n✅ تم إرسال طلبك.\nسيصلك الرد خلال فترة قصيرة.",
        parse_mode=ParseMode.HTML,
        reply_markup=None,
    )
    await _session_clear(uid)


@Client.on_callback_query(filters.regex(r"^rq\|adm\|(\d+)\|(ok|no|added)$"))
async def rq_admin_action(client: Client, cq: CallbackQuery):
    if not cq.from_user:
        return
    if Telegram.REQUESTS_ADMIN_IDS and cq.from_user.id not in Telegram.REQUESTS_ADMIN_IDS and cq.from_user.id != Telegram.OWNER_ID:
        await cq.answer("غير مصرح.", show_alert=True)
        return
    rid = int((cq.data or "").split("|", 3)[2])
    action = (cq.data or "").split("|", 3)[3]

    t = await _tracking_db()
    doc = await t["requests"].find_one({"_id": rid}) or {}
    uid = int(doc.get("user_id") or 0)
    kind = doc.get("kind") or ""

    if action == "ok":
        stamp = "✅ تم القبول"
    elif action == "no":
        stamp = "❌ تم الرفض"
    else:
        stamp = "📌 تم الإضافة"

    try:
        if cq.message.caption:
            await cq.message.edit_caption(cq.message.caption + f"\n\n{stamp}", parse_mode=ParseMode.HTML, reply_markup=None)
        else:
            await cq.message.edit_text((cq.message.text or "") + f"\n\n{stamp}", parse_mode=ParseMode.HTML, reply_markup=None, disable_web_page_preview=True)
    except Exception:
        pass

    if uid:
        extra = ""
        if kind == "tv_all":
            extra = " (جميع المواسم)"
        elif kind == "tv_season":
            extra = f" (Season {int(doc.get('season') or 0):02d})"
        elif kind == "tv_episode":
            extra = f" (S{int(doc.get('season') or 0):02d}E{int(doc.get('episode') or 0):02d})"
        try:
            await client.send_message(uid, f"{stamp}{extra}")
        except Exception:
            pass

    await cq.answer("تم.")


@Client.on_callback_query(filters.regex(r"^rq\|noop$"))
async def rq_noop(client: Client, cq: CallbackQuery):
    await cq.answer()
