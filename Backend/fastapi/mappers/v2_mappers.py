from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from Backend.config import Telegram
from Backend.helper.metadata import resolve_cover_url
from Backend.helper.pyro import get_readable_file_size
from Backend.helper.settings_manager import SettingsManager
from Backend.fastapi.schemas.v2_schemas import (
    CatalogResponse,
    EpisodeSummary,
    HeroCarouselItem,
    MediaDetail,
    MediaSummary,
    PaginationInfo,
    PlaybackDefaults,
    PlaybackManifest,
    PlaybackMeta,
    PlaybackSource,
    PlaybackSubtitle,
    SeasonSummary,
    UserMediaState,
)


# ══════════════════════════════════════════════════════════════
# §1 — HELPERS
# ══════════════════════════════════════════════════════════════
def _safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if v is None:
            return default
        return int(v)
    except (ValueError, TypeError):
        return default


def _safe_str(v: Any, default: Optional[str] = None) -> Optional[str]:
    if v is None:
        return default
    return str(v).strip() or default


def _cover(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return resolve_cover_url(value)
    except Exception:
        return value


_QUALITY_PATTERNS: List[tuple[re.Pattern, str]] = [
    (re.compile(r"4k|uhd", re.I), "4K"),
    (re.compile(r"hdr", re.I), "HDR"),
    (re.compile(r"1080|1920|fhd|full.?hd", re.I), "1080p"),
    (re.compile(r"720|1280|hd(?!r)", re.I), "720p"),
]


def _quality_tags_from_label(label: str) -> List[str]:
    if not label:
        return []
    out: List[str] = []
    for regex, tag in _QUALITY_PATTERNS:
        if regex.search(label):
            if tag not in out:
                out.append(tag)
    return out


def _best_quality_tags(telegram_list: List[Dict[str, Any]] | None) -> List[str]:
    if not telegram_list:
        return []
    tags: List[str] = []
    for q in telegram_list:
        for t in _quality_tags_from_label(str(q.get("quality") or q.get("name") or "")):
            if t not in tags:
                tags.append(t)
    return tags


# ══════════════════════════════════════════════════════════════
# §2 — MEDIA SUMMARY (the most frequently used mapper)
# ══════════════════════════════════════════════════════════════
def to_media_summary(
    doc: Dict[str, Any],
    *,
    media_type_override: Optional[str] = None,
    in_library: bool = False,
) -> MediaSummary:
    raw_type = media_type_override or doc.get("media_type") or "movie"
    media_type = "series" if raw_type in ("tv", "series", "tv_show") else "movie"

    telegram = doc.get("telegram") or []
    total_seasons = None
    total_episodes = None
    seasons = doc.get("seasons") or []
    if seasons and media_type == "series":
        total_seasons = len(seasons)
        total_episodes = 0
        for s in seasons:
            eps = s.get("episodes") or []
            total_episodes += len(eps)

    genres = doc.get("genres") or []
    if isinstance(genres, str):
        genres = [g.strip() for g in genres.split(",") if g.strip()]

    rating = None
    if doc.get("rating") is not None:
        try:
            rating = round(float(doc["rating"]), 1)
        except Exception:
            rating = None

    runtime_minutes = None
    if doc.get("runtime"):
        try:
            runtime_minutes = int(re.sub(r"\D", "", str(doc["runtime"])) or 0) or None
        except Exception:
            runtime_minutes = None

    return MediaSummary(
        tmdb_id=int(doc.get("tmdb_id") or 0),
        db_index=int(doc.get("db_index") or 1),
        media_type=media_type,
        title=_safe_str(doc.get("title"), "Untitled") or "Untitled",
        poster=_cover(doc.get("poster")),
        backdrop=_cover(doc.get("backdrop")),
        rating=rating,
        release_year=_safe_int(doc.get("release_year")),
        genres=list(genres)[:5],
        quality_tags=_best_quality_tags(telegram)[:4],
        is_anime=bool(doc.get("is_anime")),
        in_library=in_library,
        has_watch_progress=False,
        total_seasons=total_seasons,
        total_episodes=total_episodes,
        next_unwatched_ep=None,
    )


def to_hero_item(doc: Dict[str, Any], media_type_override: Optional[str] = None) -> HeroCarouselItem:
    base = to_media_summary(doc, media_type_override=media_type_override)
    overview = doc.get("description") or doc.get("overview") or ""
    if isinstance(overview, str) and len(overview) > 220:
        overview = overview[:220].rstrip() + "…"

    return HeroCarouselItem(
        tmdb_id=base.tmdb_id,
        db_index=base.db_index,
        media_type=base.media_type,
        title=base.title,
        tagline=_safe_str(doc.get("tagline")),
        backdrop=base.backdrop,
        poster=base.poster,
        rating=base.rating,
        release_year=base.release_year,
        runtime_minutes=None,
        overview_short=_safe_str(overview),
        genres=base.genres[:3],
        quality_tags=base.quality_tags,
        has_trailer=False,
    )


# ══════════════════════════════════════════════════════════════
# §3 — CATALOG RESPONSE (paginated)
# ══════════════════════════════════════════════════════════════
def to_catalog_response(
    docs: List[Dict[str, Any]],
    *,
    media_type_override: Optional[str],
    current_page: int,
    total_pages: int,
    total_items: int,
    page_size: int,
) -> CatalogResponse:
    items = [to_media_summary(d, media_type_override=media_type_override) for d in docs]
    return CatalogResponse(
        items=items,
        pagination=PaginationInfo(
            current_page=current_page,
            total_pages=total_pages,
            total_items=total_items,
            page_size=page_size,
            has_next=current_page < total_pages,
            has_prev=current_page > 1,
        ),
    )


# ══════════════════════════════════════════════════════════════
# §4 — EPISODE / SEASON → detail pages
# ══════════════════════════════════════════════════════════════
def _episode_has_stream(ep: Dict[str, Any]) -> bool:
    telegram = ep.get("telegram") or []
    return bool(telegram)


def to_episode_summary(
    ep: Dict[str, Any],
    *,
    default_backdrop: Optional[str] = None,
) -> EpisodeSummary:
    ep_num = _safe_int(ep.get("episode_number") or ep.get("episode"), 1) or 1
    ep_title = _safe_str(ep.get("title"), f"Episode {ep_num}") or f"Episode {ep_num}"
    still = _cover(ep.get("episode_backdrop") or ep.get("still")) or default_backdrop
    runtime_seconds = None
    if ep.get("runtime_seconds") or ep.get("runtime"):
        try:
            raw = ep.get("runtime_seconds") or ep.get("runtime")
            v = int(raw)
            if v < 500:  # looks like minutes → convert to seconds
                runtime_seconds = v * 60
            else:
                runtime_seconds = v
        except Exception:
            runtime_seconds = None

    return EpisodeSummary(
        episode_number=ep_num,
        title=ep_title,
        overview=_safe_str(ep.get("overview") or ep.get("description")),
        still=still,
        released=_safe_str(ep.get("released")),
        runtime_seconds=runtime_seconds,
        rating=None,
        has_stream=_episode_has_stream(ep),
        watch_progress=None,
    )


def to_season_summary(
    s: Dict[str, Any],
    *,
    show_backdrop: Optional[str] = None,
) -> SeasonSummary:
    s_num = _safe_int(s.get("season_number") or s.get("season"), 1) or 1
    episodes_raw = s.get("episodes") or []
    episodes = [to_episode_summary(e, default_backdrop=show_backdrop) for e in episodes_raw]
    return SeasonSummary(
        season_number=s_num,
        name=_safe_str(s.get("name") or s.get("title"), f"Season {s_num}") or f"Season {s_num}",
        overview=_safe_str(s.get("overview") or s.get("description")),
        poster=_cover(s.get("poster")),
        episode_count=len(episodes),
        episodes=episodes,
    )


def to_media_detail(
    doc: Dict[str, Any],
    *,
    media_type_override: Optional[str] = None,
    user_state: Optional[UserMediaState] = None,
    available_subtitle_langs: Optional[List[str]] = None,
) -> MediaDetail:
    base = to_media_summary(doc, media_type_override=media_type_override)
    raw_seasons = doc.get("seasons") or []
    seasons = [to_season_summary(s, show_backdrop=base.backdrop) for s in raw_seasons]

    updated_at = doc.get("updated_on") or doc.get("updated_at")
    if isinstance(updated_at, (int, float)):
        try:
            updated_at = datetime.utcfromtimestamp(float(updated_at))
        except Exception:
            updated_at = None
    elif isinstance(updated_at, datetime):
        pass
    else:
        updated_at = None

    return MediaDetail(
        tmdb_id=base.tmdb_id,
        db_index=base.db_index,
        media_type=base.media_type,
        imdb_id=_safe_str(doc.get("imdb_id")),
        title=base.title,
        tagline=_safe_str(doc.get("tagline")),
        poster=base.poster,
        backdrop=base.backdrop,
        logo=_cover(doc.get("logo")) if doc.get("logo") else None,
        rating=base.rating,
        release_year=base.release_year,
        runtime_minutes=None,
        total_seasons=base.total_seasons,
        total_episodes=base.total_episodes,
        overview=_safe_str(doc.get("description") or doc.get("overview")),
        genres=base.genres,
        cast=list(doc.get("cast") or [])[:12],
        studios=[],
        origin_country=list(doc.get("origin_country") or doc.get("production_countries") or [])[:6],
        original_language=_safe_str(doc.get("original_language")),
        is_anime=base.is_anime,
        quality_tags=base.quality_tags,
        trailer_youtube_id=None,
        updated_at=updated_at,
        seasons=seasons,
        user_state=user_state,
        subtitle_languages_available=list(available_subtitle_langs or []),
    )


# ══════════════════════════════════════════════════════════════
# §5 — PLAYBACK MANIFEST (sources + subtitles)
# ══════════════════════════════════════════════════════════════
def make_stream_url(
    *,
    quality_id: str,
    media_type: str,
    imdb_id: Optional[str],
    tmdb_id: Optional[int],
    db_index: Optional[int],
    token_placeholder: str = "{api_token}",
) -> str:
    slug = imdb_id or f"tmdb{tmdb_id or 0}-db{db_index or 1}"
    mt = "movie" if media_type == "movie" else "tv"
    return f"/stream/{token_placeholder}/{mt}/{slug}/{db_index or 1}/{quality_id}/master.m3u8"


def to_playback_source(
    q: Dict[str, Any],
    *,
    media_type: str,
    imdb_id: Optional[str],
    tmdb_id: int,
    db_index: int,
    index: int,
    best_client_label: str = "Telegram",
) -> PlaybackSource:
    qid = str(q.get("id") or f"q_{index}")
    label = str(q.get("quality") or q.get("name") or f"Source {index + 1}")
    parts = q.get("parts") or []
    total_bytes: Optional[int] = None
    if parts:
        try:
            total_bytes = sum(int(p.get("size_bytes") or 0) for p in parts) or None
        except Exception:
            total_bytes = None

    size_readable = None
    if total_bytes:
        try:
            size_readable = get_readable_file_size(total_bytes)
        except Exception:
            size_readable = None
    if not size_readable and q.get("size"):
        size_readable = str(q.get("size"))

    resolution = None
    low = label.lower()
    if "2160" in low or "4k" in low or "uhd" in low:
        resolution = "3840x2160"
    elif "1080" in low or "fhd" in low:
        resolution = "1920x1080"
    elif "720" in low or "hd" in low:
        resolution = "1280x720"
    elif "540" in low:
        resolution = "960x540"
    elif "480" in low or "sd" in low:
        resolution = "854x480"
    elif "360" in low:
        resolution = "640x360"

    storage = str(q.get("storage") or q.get("source") or best_client_label)

    return PlaybackSource(
        id=qid,
        label=label,
        resolution=resolution,
        codec=None,
        size_bytes=total_bytes,
        size_readable=size_readable,
        storage=storage,
        audio_languages=[],
        priority=index + 1,
        stream_url=make_stream_url(
            quality_id=qid,
            media_type=media_type,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            db_index=db_index,
        ),
    )


def _build_manifest(
    *,
    media_type: str,
    title: str,
    full_title: Optional[str],
    backdrop: Optional[str],
    runtime_seconds: Optional[int],
    telegram_sources: List[Dict[str, Any]],
    subtitles: List[Dict[str, Any]],
    imdb_id: Optional[str],
    tmdb_id: int,
    db_index: int,
    show_title: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
    next_episode: Optional[Dict[str, Any]] = None,
    prev_episode: Optional[Dict[str, Any]] = None,
) -> PlaybackManifest:
    sources = [
        to_playback_source(
            q,
            media_type=media_type,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            db_index=db_index,
            index=i,
        )
        for i, q in enumerate(telegram_sources)
    ]

    sub_items: List[PlaybackSubtitle] = []
    default_picked = False
    for i, s in enumerate(subtitles or []):
        lang = _safe_str(s.get("lang_code") or s.get("language") or "en", "en") or "en"
        label = _safe_str(s.get("lang_label") or s.get("name") or lang, lang) or lang
        is_default = (lang.lower() in ("ar", "arabic") and not default_picked) or i == 0 and not default_picked
        if is_default:
            default_picked = True
        url = _safe_str(
            s.get("serve_url")
            or s.get("url")
            or (f"/api/v2/subtitles/{_safe_str(s.get('_id') or s.get('id'), i)}/serve" if s.get("_id") or s.get("id") else None)
        )
        sub_items.append(
            PlaybackSubtitle(
                id=_safe_str(s.get("_id") or s.get("id") or f"sub_{i}", f"sub_{i}") or f"sub_{i}",
                label=label,
                lang_code=lang,
                default=is_default,
                forced=bool(s.get("forced")),
                format="vtt",
                url=url or "#",
            )
        )

    return PlaybackManifest(
        meta=PlaybackMeta(
            title=title,
            full_title=full_title,
            show_title=show_title,
            season=season,
            episode=episode,
            media_type=media_type,
            backdrop=backdrop,
            runtime_seconds=runtime_seconds,
            next_episode=next_episode,
            prev_episode=prev_episode,
            intro_skip_range=None,
            credits_start=None,
        ),
        sources=sources,
        subtitles=sub_items,
        user_defaults=PlaybackDefaults(
            preferred_quality_id=None,
            preferred_subtitle_id=None if not sub_items else sub_items[0].id,
            auto_select_best_quality=True,
            auto_play_next=True,
        ),
    )


def build_manifest_from_movie_doc(
    doc: Dict[str, Any],
    *,
    subtitles: Optional[List[Dict[str, Any]]] = None,
) -> PlaybackManifest:
    title = _safe_str(doc.get("title"), "Untitled") or "Untitled"
    db_index = int(doc.get("db_index") or 1)
    telegram = list(doc.get("telegram") or [])
    return _build_manifest(
        media_type="movie",
        title=title,
        full_title=title,
        backdrop=_cover(doc.get("backdrop")),
        runtime_seconds=None,
        telegram_sources=telegram,
        subtitles=list(subtitles or []),
        imdb_id=_safe_str(doc.get("imdb_id")),
        tmdb_id=int(doc.get("tmdb_id") or 0),
        db_index=db_index,
    )


def build_manifest_from_episode(
    show_doc: Dict[str, Any],
    *,
    season_number: int,
    episode_number: int,
    subtitles: Optional[List[Dict[str, Any]]] = None,
) -> PlaybackManifest:
    show_title = _safe_str(show_doc.get("title"), "Untitled") or "Untitled"
    db_index = int(show_doc.get("db_index") or 1)
    target_season = None
    for s in show_doc.get("seasons") or []:
        if int(s.get("season_number") or s.get("season") or -1) == int(season_number):
            target_season = s
            break
    target_ep = None
    prev_ep = None
    next_ep = None
    if target_season:
        eps = list(target_season.get("episodes") or [])
        for idx, e in enumerate(eps):
            if int(e.get("episode_number") or e.get("episode") or -1) == int(episode_number):
                target_ep = e
                if idx > 0:
                    p = eps[idx - 1]
                    prev_ep = {
                        "season": int(target_season.get("season_number") or season_number),
                        "episode": int(p.get("episode_number") or p.get("episode") or idx),
                        "title": p.get("title") or f"Episode {idx}",
                    }
                if idx < len(eps) - 1:
                    n = eps[idx + 1]
                    next_ep = {
                        "season": int(target_season.get("season_number") or season_number),
                        "episode": int(n.get("episode_number") or n.get("episode") or idx + 2),
                        "title": n.get("title") or f"Episode {idx + 2}",
                    }
                break

    ep_title = (
        (target_ep and _safe_str(target_ep.get("title"), f"Episode {episode_number}"))
        or f"Episode {episode_number}"
    )
    meta_title = f"S{int(season_number):02d} · E{int(episode_number):02d} · {ep_title}"
    full = f"{show_title} · {ep_title}"
    telegram = list(target_ep.get("telegram") or []) if target_ep else []

    return _build_manifest(
        media_type="series",
        title=meta_title,
        full_title=full,
        backdrop=_cover(
            (target_ep and (target_ep.get("episode_backdrop") or target_ep.get("still")))
            or show_doc.get("backdrop")
        ),
        runtime_seconds=None,
        telegram_sources=telegram,
        subtitles=list(subtitles or []),
        imdb_id=_safe_str(show_doc.get("imdb_id")),
        tmdb_id=int(show_doc.get("tmdb_id") or 0),
        db_index=db_index,
        show_title=show_title,
        season=int(season_number),
        episode=int(episode_number),
        next_episode=next_ep,
        prev_episode=prev_ep,
    )


# ══════════════════════════════════════════════════════════════
# §6 — USER / SETTINGS
# ══════════════════════════════════════════════════════════════
def _sub_status_from_doc(user_doc: Optional[Dict[str, Any]]) -> str:
    if not user_doc:
        return "inactive"
    status = str(user_doc.get("subscription_status") or "inactive").lower()
    if status in ("active", "expired", "trial", "inactive"):
        return status
    expires = user_doc.get("subscription_expiry")
    if not expires:
        return "inactive"
    try:
        if expires < datetime.utcnow():
            return "expired"
        return "active"
    except Exception:
        return "inactive"


def to_user_profile(user_doc: Optional[Dict[str, Any]]) -> "UserProfile":  # noqa: F821 - Pydantic imported at module bottom
    from Backend.fastapi.schemas.v2_schemas import UserProfile

    if not user_doc:
        return UserProfile(
            id=0,
            display_name="Guest",
            subscription_status="inactive",
            is_admin=False,
        )
    user_id_raw = user_doc.get("_id") or user_doc.get("id") or 0
    try:
        user_id = int(user_id_raw)
    except Exception:
        try:
            user_id = int(str(user_id_raw).replace("user_", ""))
        except Exception:
            user_id = 0
    display = (
        _safe_str(user_doc.get("display_name"))
        or _safe_str(user_doc.get("first_name"))
        or _safe_str(user_doc.get("username"))
        or f"User {user_id}"
    )
    avatar_url = None
    try:
        if user_doc.get("photo_url"):
            avatar_url = str(user_doc.get("photo_url"))
    except Exception:
        pass

    owner = False
    try:
        owner_id = int(Telegram.OWNER_ID)
        owner = user_id == owner_id or bool(user_doc.get("is_admin"))
    except Exception:
        owner = bool(user_doc.get("is_admin"))

    return UserProfile(
        id=user_id,
        email=_safe_str(user_doc.get("email")),
        display_name=display,
        avatar_url=avatar_url,
        subscription_status=_sub_status_from_doc(user_doc),  # type: ignore[arg-type]
        subscription_expires_at=user_doc.get("subscription_expiry"),
        is_admin=owner,
        created_at=user_doc.get("created_at"),
    )


def make_stremio_masked(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    if len(token) <= 8:
        return "*" * len(token)
    return f"****_{token[-8:]}"


def base_url_for_links() -> str:
    try:
        s = SettingsManager.current()
        return str(getattr(s, "base_url", "")).rstrip("/")
    except Exception:
        return ""
