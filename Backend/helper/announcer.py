from __future__ import annotations

from asyncio import create_task
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait, MessageDeleteForbidden, MessageIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from Backend import db
from Backend.helper import notification_i18n as i18n
from Backend.helper.settings_manager import SettingsManager
from Backend.logger import LOGGER
from Backend.pyrofork.bot import StreamBot, get_streambot_url


#----- Accept either a numeric channel id (-100...) or an @username
def _resolve_chat(value: str):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value


#----- Atomically claim a title so it is announced at most once; returns True if newly claimed
async def _claim(media_type: str, tmdb_id) -> bool:
    if not tmdb_id:
        return False
    result = await db.dbs["tracking"]["announced"].update_one(
        {"_id": f"{media_type}:{tmdb_id}"},
        {"$setOnInsert": {"at": datetime.utcnow()}},
        upsert=True,
    )
    return result.upserted_id is not None


async def _store_announcement_msg(media_type: str, tmdb_id, chat_id, message_id: int) -> None:
    if not tmdb_id or not message_id:
        return
    try:
        await db.dbs["tracking"]["announced"].update_one(
            {"_id": f"{media_type}:{tmdb_id}"},
            {"$set": {"chat_id": chat_id, "message_id": message_id, "at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        LOGGER.warning(f"Failed to store announcement message id: {e}")


# ====================================================================
# Shared notification payload – built once, formatted per platform
# ====================================================================

def _round_rating(val) -> Optional[float]:
    try:
        if val is None:
            return None
        v = float(val)
        if v <= 0:
            return None
        return round(v, 1)
    except (TypeError, ValueError):
        return None


async def _build_payload(info: Dict[str, Any], event: str) -> Dict[str, Any]:
    """Build notification payload with all metadata."""
    settings = SettingsManager.current()
    lang = settings.metadata_language
    is_tv = info.get("media_type") == "tv"
    is_anime = bool(info.get("is_anime"))

    title = info.get("title") or "Unknown"
    year = info.get("year") or None

    try:
        season = int(info.get("season_number")) if info.get("season_number") is not None else None
    except (TypeError, ValueError):
        season = None
    try:
        episode = int(info.get("episode_number")) if info.get("episode_number") is not None else None
    except (TypeError, ValueError):
        episode = None

    season_episode_label = _build_season_episode_text(season, episode, lang)

    quality = info.get("quality") or ""
    file_size = info.get("size") or ""
    genres_raw = info.get("genres") or []
    genres = i18n.localize_genres(genres_raw, lang)

    rating = _round_rating(info.get("rate"))
    episode_rating = _round_rating(info.get("episode_rating"))

    # Get story and translate if needed
    description = (info.get("description") or info.get("episode_overview") or "").strip()
    if len(description) > 400:
        description = description[:397].rstrip() + "..."

    # Translate story if target is not English and story appears to be English
    if lang != "EN" and description:
        description = await _maybe_translate_story(description, lang)

    poster = info.get("backdrop") or info.get("poster") or ""

    event_label = _get_event_label(is_tv, event, lang)

    return {
        "event": event,
        "event_label": event_label,
        "lang": lang,
        "media_type": info.get("media_type"),
        "is_tv": is_tv,
        "is_anime": is_anime,
        "tmdb_id": info.get("tmdb_id"),
        "imdb_id": info.get("imdb_id"),
        "title": title,
        "year": year,
        "season_episode": season_episode_label,
        "quality": quality,
        "file_size": file_size,
        "genres": genres,
        "rating": rating,
        "episode_rating": episode_rating,
        "story": description,
        "poster": poster,
        "info_raw": info,
    }


def _build_season_episode_text(season: Optional[int], episode: Optional[int], lang: str) -> str:
    """Build localized season/episode text like 'الموسم 4 - الحلقة 5'."""
    if season is None or episode is None:
        return ""

    season_label = i18n.label("season", lang)
    episode_label = i18n.label("episode", lang)

    # Format: "الموسم 4 - الحلقة 5" or "Season 4 - Episode 5"
    return f"{season_label} {season} - {episode_label} {episode}"


def _get_event_label(is_tv: bool, event: str, lang: str) -> str:
    """Get appropriate event label with emoji based on media type."""
    if is_tv:
        return i18n.label("tv_add" if event == "add" else "event_remove", lang)
    return i18n.label("event_add" if event == "add" else "event_remove", lang)


async def _maybe_translate_story(story: str, lang: str) -> str:
    """Translate story if needed using Google Translate."""
    if not story or not story.strip():
        return story
    try:
        return await i18n.translate_story_if_needed(story, lang)
    except Exception:
        # On any error, return original
        return story


# ====================================================================
# Telegram formatting (HTML, existing Pyrogram buttons preserved)
# ====================================================================

def _build_markup(info: Dict[str, Any]):
    rows = []
    base = SettingsManager.current().base_url
    imdb_id = str(info.get("imdb_id") or "").strip()
    stremio_type = "series" if info.get("media_type") == "tv" else "movie"
    if base and imdb_id:
        rows.append([
            InlineKeyboardButton("▶️ Stremio", url=f"{base}/open/stremio/{stremio_type}/{imdb_id}"),
            InlineKeyboardButton("📱 Nuvio", url=f"{base}/open/nuvio/{stremio_type}/{imdb_id}"),
        ])
    bot_url = get_streambot_url()
    if bot_url and bot_url != "https://t.me/":
        rows.append([InlineKeyboardButton("🤖 Get Addon", url=bot_url)])
    return InlineKeyboardMarkup(rows) if rows else None


def _format_telegram_caption(p: Dict[str, Any]) -> str:
    lang = p["lang"]
    # Media type icon: 🎬 for movies, 📺 for TV
    media_icon = "📺" if p["is_tv"] else "🎬"
    lines = [f"<b>{p['event_label']}</b>", ""]

    header = f"{media_icon} {p['title']}"
    if p["year"]:
        header += f" ({p['year']})"
    lines.append(header)

    if p["is_tv"] and p["season_episode"]:
        lines.append("")
        lines.append(p["season_episode"])

    lines.append("")
    if p["quality"]:
        lines.append(f"<b>{i18n.label('quality', lang)}</b> {p['quality']}")
    if p["file_size"]:
        lines.append(f"<b>{i18n.label('file_size', lang)}</b> {p['file_size']}")
    if p["genres"]:
        lines.append(f"<b>{i18n.label('genres', lang)}</b> {', '.join(p['genres'][:4])}")

    if p["is_tv"] and p["episode_rating"] is not None:
        lines.append(f"<b>{i18n.label('episode_rating', lang)}</b> {p['episode_rating']}")
    elif p["rating"] is not None:
        lines.append(f"<b>{i18n.label('rating', lang)}</b> {p['rating']}")

    if p["story"]:
        lines.append("")
        lines.append(f"<b>{i18n.label('story', lang)}</b>")
        lines.append(f"<i>{p['story']}</i>")

    return "\n".join(lines)


# ====================================================================
# Discord formatting (embed)
# ====================================================================

def _format_rating_line(p: Dict[str, Any]) -> str:
    if p["is_tv"] and p["episode_rating"] is not None:
        return f"**{i18n.label('episode_rating', p['lang'])}** {p['episode_rating']}"
    if p["rating"] is not None:
        return f"**{i18n.label('rating', p['lang'])}** {p['rating']}"
    return ""


def _build_discord_embed(p: Dict[str, Any]) -> Dict[str, Any]:
    lang = p["lang"]
    desc_parts = []

    header = p["title"]
    if p["year"]:
        header += f" ({p['year']})"
    if p["is_tv"] and p["season_episode"]:
        header += f"  •  {p['season_episode']}"
    desc_parts.append(f"**{header}**")
    desc_parts.append("")

    if p["quality"]:
        desc_parts.append(f"**{i18n.label('quality', lang)}** {p['quality']}")
    if p["file_size"]:
        desc_parts.append(f"**{i18n.label('file_size', lang)}** {p['file_size']}")
    if p["genres"]:
        desc_parts.append(f"**{i18n.label('genres', lang)}** {', '.join(p['genres'][:4])}")
    rating_line = _format_rating_line(p)
    if rating_line:
        desc_parts.append(rating_line)
    if p["story"]:
        desc_parts.append("")
        desc_parts.append(f"**{i18n.label('story', lang)}**")
        desc_parts.append(p["story"])

    embed = {
        "title": p["event_label"],
        "description": "\n".join(desc_parts),
        "color": 0x2ECC71 if p["event"] == "add" else 0xE74C3C,
    }
    if p["poster"]:
        embed["image"] = {"url": p["poster"]}

    fields = []
    base = SettingsManager.current().base_url
    imdb_id = str(p.get("imdb_id") or "").strip()
    stremio_type = "series" if p["is_tv"] else "movie"
    if base and imdb_id:
        fields.append({
            "name": "Links",
            "value": f"[Stremio]({base}/open/stremio/{stremio_type}/{imdb_id})  •  [Nuvio]({base}/open/nuvio/{stremio_type}/{imdb_id})",
            "inline": False,
        })
    bot_url = get_streambot_url()
    if bot_url and bot_url != "https://t.me/":
        fields.append({
            "name": "Addon",
            "value": f"[Get Addon]({bot_url})",
            "inline": False,
        })
    if fields:
        embed["fields"] = fields

    return embed


# ====================================================================
# Delivery
# ====================================================================

async def _deliver_telegram_legacy(p: Dict[str, Any]) -> Optional[int]:
    """Deliver via the existing StreamBot + announcement_channel (original flow).
    Returns the message id on success so it can be stored for later deletion."""
    settings = SettingsManager.current()
    if not settings.announce_new_content:
        return None
    chat = _resolve_chat(settings.announcement_channel)
    if chat is None:
        return None

    caption = _format_telegram_caption(p)
    poster = p["poster"]
    markup = _build_markup(p["info_raw"])

    try:
        sent = None
        if poster:
            try:
                sent = await StreamBot.send_photo(
                    chat, poster, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            except FloodWait:
                raise
            except Exception:
                sent = None
        if sent is None:
            sent = await StreamBot.send_message(
                chat, caption, parse_mode=ParseMode.HTML,
                reply_markup=markup, disable_web_page_preview=True,
            )
        return getattr(sent, "id", None)
    except FloodWait as e:
        LOGGER.warning(f"[Announcer] Telegram FloodWait {e.value}s")
    except Exception as e:
        LOGGER.error(f"[Announcer] Telegram delivery failed for '{p['title']}': {e}")
    return None


async def _deliver_telegram_direct(p: Dict[str, Any]) -> None:
    """Deliver to a separately-configured Telegram chat via raw Bot API HTTP (httpx).
    Uses notification_bot_token + notification_chat_id when both are set."""
    settings = SettingsManager.current()
    token = settings.notification_bot_token
    chat_id = settings.notification_chat_id
    if not token or not chat_id:
        return

    caption = _format_telegram_caption(p)
    poster = p["poster"]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if poster:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                payload = {
                    "chat_id": chat_id,
                    "photo": poster,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return
                LOGGER.warning(
                    f"[Announcer] Direct Telegram sendPhoto failed {resp.status_code}: {resp.text[:200]}"
                )
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                LOGGER.warning(
                    f"[Announcer] Direct Telegram sendMessage failed {resp.status_code}: {resp.text[:200]}"
                )
    except Exception as e:
        LOGGER.error(f"[Announcer] Direct Telegram delivery failed for '{p['title']}': {e}")


async def _deliver_discord(p: Dict[str, Any]) -> None:
    settings = SettingsManager.current()
    webhook = settings.discord_webhook_url
    if not webhook:
        return
    try:
        embed = _build_discord_embed(p)
        payload = {"embeds": [embed]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(webhook, json=payload)
            if resp.status_code not in (200, 204):
                LOGGER.warning(
                    f"[Announcer] Discord webhook failed {resp.status_code}: {resp.text[:200]}"
                )
    except Exception as e:
        LOGGER.error(f"[Announcer] Discord delivery failed for '{p['title']}': {e}")


def _event_allowed(event: str) -> bool:
    pref = SettingsManager.current().notification_events
    if pref == "both":
        return True
    return pref == event


# ====================================================================
# Main orchestration
# ====================================================================

async def _announce_event(info: Dict[str, Any], event: str) -> None:
    if not _event_allowed(event):
        return

    p = await _build_payload(dict(info), event)

    if event == "add":
        # Try all enabled providers first, then claim only if at least one succeeds
        any_success = False
        legacy_msg_id = None
        legacy_chat_id = None

        # --- Telegram Legacy (original flow) ---
        try:
            settings = SettingsManager.current()
            if settings.announce_new_content:
                chat = _resolve_chat(settings.announcement_channel)
                if chat is not None:
                    msg_id = await _deliver_telegram_legacy(p)
                    if msg_id:
                        any_success = True
                        legacy_msg_id = msg_id
                        legacy_chat_id = chat
        except Exception as e:
            LOGGER.error(f"[Announcer] Telegram legacy exception (non-fatal): {e}")

        # --- Telegram Direct (separate bot token) ---
        try:
            settings = SettingsManager.current()
            if settings.notification_bot_token and settings.notification_chat_id:
                await _deliver_telegram_direct(p)
                any_success = True
        except Exception as e:
            LOGGER.error(f"[Announcer] Telegram direct exception (non-fatal): {e}")

        # --- Discord Webhook ---
        try:
            settings = SettingsManager.current()
            if settings.discord_webhook_url:
                await _deliver_discord(p)
                any_success = True
        except Exception as e:
            LOGGER.error(f"[Announcer] Discord exception (non-fatal): {e}")

        # --- Claim and store only if at least one provider succeeded ---
        if any_success:
            try:
                await _claim(p["media_type"], p["tmdb_id"])
                if legacy_msg_id and legacy_chat_id:
                    await _store_announcement_msg(p["media_type"], p["tmdb_id"], legacy_chat_id, legacy_msg_id)
            except Exception as e:
                LOGGER.warning(f"[Announcer] Failed to claim/store after successful delivery: {e}")

    else:
        # --- Remove event: all providers independently ---
        # Telegram Legacy
        try:
            settings = SettingsManager.current()
            if settings.announce_new_content and settings.announcement_channel:
                await _deliver_telegram_legacy(p)
        except Exception as e:
            LOGGER.error(f"[Announcer] Telegram legacy remove exception (non-fatal): {e}")

        # Telegram Direct
        try:
            settings = SettingsManager.current()
            if settings.notification_bot_token and settings.notification_chat_id:
                await _deliver_telegram_direct(p)
        except Exception as e:
            LOGGER.error(f"[Announcer] Telegram direct remove exception (non-fatal): {e}")

        # Discord
        try:
            settings = SettingsManager.current()
            if settings.discord_webhook_url:
                await _deliver_discord(p)
        except Exception as e:
            LOGGER.error(f"[Announcer] Discord remove exception (non-fatal): {e}")


# ====================================================================
# Public API – backward compatible entry points
# ====================================================================

def announce_new_media(info: dict) -> None:
    try:
        create_task(_announce_event(dict(info), "add"))
    except RuntimeError:
        LOGGER.warning("Announcement skipped: no running event loop.")


def announce_removed_media(info: dict) -> None:
    try:
        create_task(_announce_event(dict(info), "remove"))
    except RuntimeError:
        LOGGER.warning("Remove announcement skipped: no running event loop.")


async def delete_announcement(media_type: str, tmdb_id) -> None:
    if not tmdb_id:
        return
    key = f"{media_type}:{tmdb_id}"
    try:
        doc = await db.dbs["tracking"]["announced"].find_one_and_delete({"_id": key})
    except Exception as e:
        LOGGER.warning(f"Failed to lookup announcement for {key}: {e}")
        return
    if not doc:
        return
    chat_id = doc.get("chat_id")
    message_id = doc.get("message_id")
    if not chat_id or not message_id:
        return
    try:
        await StreamBot.delete_messages(chat_id, message_id)
        LOGGER.info(f"Deleted announcement message {message_id} for {key}")
    except (MessageDeleteForbidden, MessageIdInvalid) as e:
        LOGGER.warning(f"Could not delete announcement {message_id} for {key}: {e}")
    except FloodWait as e:
        LOGGER.warning(f"FloodWait deleting announcement for {key}: {e.value}s")
    except Exception as e:
        LOGGER.warning(f"Failed to delete announcement message for {key}: {e}")


def delete_announcement_async(media_type: str, tmdb_id) -> None:
    try:
        create_task(delete_announcement(media_type, tmdb_id))
    except RuntimeError:
        LOGGER.warning("Announcement delete skipped: no running event loop.")
