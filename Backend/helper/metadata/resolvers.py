"""Priority chains for metadata resolution.

Anime  : Kitsu > TVDB > TMDB > Cinemeta
Movies : TMDB > Cinemeta
Series : TVDB > Cinemeta > TMDB
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from Backend.helper.metadata.common import split_default_id, title_similarity, CINEMETA_THRESHOLD
from Backend.helper.metadata.providers import cinemeta, kitsu, tmdb, tvdb
from Backend.logger import LOGGER


# ── Localization helpers ──────────────────────────────────────────────────────

_DESCRIPTION_ONLY_FIELDS = frozenset(["description", "episode_overview"])

_TRANSLATABLE_TEXT_FIELDS = frozenset([
    "title",
    "title_english",
    "original_title",
    "description",
    "episode_title",
    "episode_overview",
    "runtime",
])

_TRANSLATABLE_LIST_TEXT_FIELDS = frozenset(["genres", "cast"])


def _is_non_empty_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return False


def _is_non_empty_list(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes)) and bool(list(value))


def _fallback_field(local_value: Any, english_value: Any) -> Any:
    """Field-level fallback: use localized if present; otherwise keep English.

    Never replaces IDs, numeric fields, URLs, encoded strings etc.
    """
    if _is_non_empty_list(local_value):
        return local_value
    if _is_non_empty_list(english_value) and not _is_non_empty_list(local_value):
        return english_value
    if _is_non_empty_text(local_value):
        return local_value
    return english_value if (english_value is not None) else local_value


def _apply_field_fallback(localized: Dict[str, Any], english_base: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict that blends localized values with the English base.

    Non-translatable fields (IDs, ratings, numbers, encoded strings, URLs, media
    metadata etc.) are ALWAYS copied untouched from ``english_base`` so that a
    partial localized response can never wipe a required technical field.
    """
    result: Dict[str, Any] = dict(english_base)
    for key in _TRANSLATABLE_TEXT_FIELDS:
        if key in localized or key in english_base:
            result[key] = _fallback_field(localized.get(key), english_base.get(key))
    for key in _TRANSLATABLE_LIST_TEXT_FIELDS:
        if key in localized or key in english_base:
            result[key] = _fallback_field(localized.get(key), english_base.get(key))
    return result


def _merge_description_only(english_base: Dict[str, Any], localized: Dict[str, Any]) -> Dict[str, Any]:
    """Keep ``english_base`` verbatim but override the description fields.

    Titles, genres, cast, episode titles etc. all remain in English — only the
    whitelisted description fields can come from the localized payload.
    """
    result = dict(english_base)
    for key in _DESCRIPTION_ONLY_FIELDS:
        loc_val = localized.get(key)
        if _is_non_empty_text(loc_val):
            result[key] = loc_val
    return result


# ── Movies: TMDB > Cinemeta ──────────────────────────────────────────────────

async def resolve_movie(
    title: str,
    encoded_string,
    year=None,
    quality=None,
    default_id=None,
    *,
    metadata_language: str = "en",
    metadata_language_scope: str = "all",
) -> Optional[dict]:
    imdb_id, tmdb_id, explicit_imdb, force_tmdb = split_default_id(default_id)
    lang = str(metadata_language or "en").strip().lower()
    scope = "description_only" if str(metadata_language_scope or "all").lower() == "description_only" else "all"

    # Explicit TMDB id
    if tmdb_id and force_tmdb:
        payload = await _tmdb_movie_payload(tmdb_id, quality, encoded_string, lang=lang, scope=scope)
        if payload:
            return payload

    # Explicit IMDb id → Cinemeta (always English)
    if imdb_id and explicit_imdb:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "movie")
            if detail:
                return cinemeta.build_movie_payload(detail, imdb_id, title, quality, encoded_string)
        except Exception as e:
            LOGGER.warning(f"Cinemeta explicit movie fetch failed [{imdb_id}]: {e}")

    # 1) TMDB first
    if not tmdb_id:
        hit = await tmdb.safe_search(title, "movie", year)
        if hit:
            tmdb_id = hit.id
    if tmdb_id:
        payload = await _tmdb_movie_payload(tmdb_id, quality, encoded_string, lang=lang, scope=scope)
        if payload:
            LOGGER.info(f"[MOVIE] TMDB hit for '{title}' (year={year}) [lang={lang} scope={scope}]")
            return payload

    # 2) Cinemeta fallback (always English)
    LOGGER.info(f"[MOVIE] TMDB miss for '{title}' -> Cinemeta")
    if not imdb_id:
        imdb_id = await cinemeta.safe_search(title, "movie", year)
    if imdb_id:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "movie")
            if detail:
                sim = title_similarity(title, detail.get("title", ""))
                if sim >= CINEMETA_THRESHOLD or explicit_imdb:
                    return cinemeta.build_movie_payload(detail, imdb_id, title, quality, encoded_string)
                LOGGER.info(
                    f"[MOVIE] Cinemeta title mismatch for '{title}': "
                    f"got '{detail.get('title')}' (sim={sim:.2f})"
                )
        except Exception as e:
            LOGGER.warning(f"Cinemeta movie fetch failed [{title}]: {e}")

    LOGGER.info(f"[MOVIE] No metadata for '{title}' (year={year})")
    return None


async def _tmdb_movie_payload(tmdb_id, quality, encoded_string, *, lang: str, scope: str) -> Optional[dict]:
    """Fetch a TMDB movie and apply language + scope rules with field fallback."""
    try:
        if scope == "all" and lang != "en":
            # Two fetches to guarantee we always have a pristine English base
            # for technical fields and reliable field-level fallback.
            en_det = await tmdb.details("movie", tmdb_id, language="en")
            loc_det = await tmdb.details("movie", tmdb_id, language=lang)
            if not en_det:
                return None
            en_payload = tmdb.build_movie_payload(en_det, quality, encoded_string)
            if loc_det:
                loc_payload = tmdb.build_movie_payload(loc_det, quality, encoded_string)
            else:
                loc_payload = {}
            return _apply_field_fallback(loc_payload, en_payload)

        if scope == "description_only" and lang != "en":
            en_det = await tmdb.details("movie", tmdb_id, language="en")
            if not en_det:
                return None
            en_payload = tmdb.build_movie_payload(en_det, quality, encoded_string)
            loc_det = await tmdb.details("movie", tmdb_id, language=lang)
            if loc_det:
                loc_payload = tmdb.build_movie_payload(loc_det, quality, encoded_string)
                return _merge_description_only(en_payload, loc_payload)
            return en_payload

        # English path (scope=all or scope=description_only when lang="en")
        det = await tmdb.details("movie", tmdb_id, language="en")
        if not det:
            return None
        return tmdb.build_movie_payload(det, quality, encoded_string)
    except Exception as e:
        LOGGER.warning(f"[MOVIE] TMDB localized payload build failed id={tmdb_id} [lang={lang}]: {e}")
        # Ultimate fall-back to plain English (if scope=all and loc fails we still
        # want the movie to be ingest-able).
        try:
            det = await tmdb.details("movie", tmdb_id, language="en")
            if det:
                return tmdb.build_movie_payload(det, quality, encoded_string)
        except Exception as e2:
            LOGGER.warning(f"[MOVIE] TMDB English fall-back failed id={tmdb_id}: {e2}")
        return None


# ── Series: TVDB > Cinemeta > TMDB ────────────────────────────────────────────

async def resolve_series(
    title: str,
    season: int,
    episode: int,
    encoded_string,
    year=None,
    quality=None,
    default_id=None,
    *,
    metadata_language: str = "en",
    metadata_language_scope: str = "all",
) -> Optional[dict]:
    imdb_id, tmdb_id, explicit_imdb, force_tmdb = split_default_id(default_id)
    lang = str(metadata_language or "en").strip().lower()
    scope = "description_only" if str(metadata_language_scope or "all").lower() == "description_only" else "all"

    # Explicit overrides skip the chain
    if tmdb_id and force_tmdb:
        payload = await _tmdb_tv_payload(tmdb_id, season, episode, quality, encoded_string, lang=lang, scope=scope)
        if payload:
            return payload

    if imdb_id and explicit_imdb:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "tvSeries")
            ep = await cinemeta.cached_season(imdb_id, season, episode)
            if detail:
                return cinemeta.build_tv_payload(
                    detail, ep or {}, imdb_id, title, season, episode, quality, encoded_string
                )
        except Exception as e:
            LOGGER.warning(f"Cinemeta explicit TV fetch failed [{imdb_id}]: {e}")

    # 1) TVDB (native language + scope support in its builder)
    try:
        result = await tvdb.fetch_series_metadata(
            title, season, episode, encoded_string,
            year=year, quality=quality,
            language=lang, language_scope=scope,
        )
        if result:
            LOGGER.info(f"[SERIES] TVDB hit for '{title}' S{season:02d}E{episode:02d} [lang={lang} scope={scope}]")
            return result
    except Exception as e:
        LOGGER.warning(f"[SERIES] TVDB error for '{title}': {e}")

    # 2) Cinemeta (always English)
    LOGGER.info(f"[SERIES] TVDB miss for '{title}' -> Cinemeta")
    if not imdb_id:
        imdb_id = await cinemeta.safe_search(title, "tvSeries", year)
    if imdb_id:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "tvSeries")
            ep = await cinemeta.cached_season(imdb_id, season, episode)
            if detail:
                sim = title_similarity(title, detail.get("title", ""))
                if sim >= CINEMETA_THRESHOLD or explicit_imdb:
                    return cinemeta.build_tv_payload(
                        detail, ep or {}, imdb_id, title, season, episode, quality, encoded_string
                    )
                LOGGER.info(
                    f"[SERIES] Cinemeta title mismatch for '{title}': "
                    f"got '{detail.get('title')}' (sim={sim:.2f})"
                )
        except Exception as e:
            LOGGER.warning(f"Cinemeta TV fetch failed [{title}]: {e}")

    # 3) TMDB
    LOGGER.info(f"[SERIES] Cinemeta miss for '{title}' -> TMDB")
    if not tmdb_id:
        hit = await tmdb.safe_search(title, "tv", year)
        if hit:
            tmdb_id = hit.id
    if tmdb_id:
        payload = await _tmdb_tv_payload(tmdb_id, season, episode, quality, encoded_string, lang=lang, scope=scope)
        if payload:
            return payload

    LOGGER.info(f"[SERIES] No metadata for '{title}' S{season:02d}E{episode:02d}")
    return None


async def _tmdb_tv_payload(tmdb_id, season, episode, quality, encoded_string, *, lang: str, scope: str) -> Optional[dict]:
    """Fetch a TMDB series/episode pair applying language + scope + fallback rules."""
    try:
        if scope == "all" and lang != "en":
            en_tv = await tmdb.details("tv", tmdb_id, language="en")
            en_ep = await tmdb.episode_details(tmdb_id, season, episode, language="en")
            if not en_tv:
                return None
            en_payload = tmdb.build_tv_payload(en_tv, en_ep, season, episode, quality, encoded_string)
            loc_tv = await tmdb.details("tv", tmdb_id, language=lang)
            loc_ep = await tmdb.episode_details(tmdb_id, season, episode, language=lang)
            if loc_tv:
                loc_payload = tmdb.build_tv_payload(loc_tv, loc_ep, season, episode, quality, encoded_string)
            else:
                loc_payload = {}
            return _apply_field_fallback(loc_payload, en_payload)

        if scope == "description_only" and lang != "en":
            en_tv = await tmdb.details("tv", tmdb_id, language="en")
            en_ep = await tmdb.episode_details(tmdb_id, season, episode, language="en")
            if not en_tv:
                return None
            en_payload = tmdb.build_tv_payload(en_tv, en_ep, season, episode, quality, encoded_string)
            loc_tv = await tmdb.details("tv", tmdb_id, language=lang)
            loc_ep = await tmdb.episode_details(tmdb_id, season, episode, language=lang)
            if loc_tv:
                loc_payload = tmdb.build_tv_payload(loc_tv, loc_ep, season, episode, quality, encoded_string)
                return _merge_description_only(en_payload, loc_payload)
            return en_payload

        tv = await tmdb.details("tv", tmdb_id, language="en")
        ep = await tmdb.episode_details(tmdb_id, season, episode, language="en")
        if not tv:
            return None
        return tmdb.build_tv_payload(tv, ep, season, episode, quality, encoded_string)
    except Exception as e:
        LOGGER.warning(f"[SERIES] TMDB localized payload build failed id={tmdb_id} [lang={lang}]: {e}")
        try:
            tv = await tmdb.details("tv", tmdb_id, language="en")
            ep = await tmdb.episode_details(tmdb_id, season, episode, language="en")
            if tv:
                return tmdb.build_tv_payload(tv, ep, season, episode, quality, encoded_string)
        except Exception as e2:
            LOGGER.warning(f"[SERIES] TMDB English fall-back failed id={tmdb_id}: {e2}")
        return None


# ── Anime: Kitsu > TVDB > TMDB > Cinemeta ─────────────────────────────────────

async def resolve_anime_tv(
    title: str,
    season,
    episode: int,
    encoded_string,
    year=None,
    quality=None,
    absolute: bool = False,
    *,
    metadata_language: str = "en",
    metadata_language_scope: str = "all",
) -> Optional[dict]:
    """Resolve anime episode metadata.

    absolute=True (or season is None): orphan/absolute numbering
    e.g. "One Piece 1223 720.mkv" → Kitsu absolute episode 1223.

    NOTE: Kitsu is kept as the highest-priority anime resolver but this feature
    deliberately does NOT attempt to localize Kitsu payloads. Anime titles and
    descriptions usually require specialized handling and the user-requested
    behaviour is scoped to TMDB/TVDB. Kitsu results fall through unchanged.
    """
    absolute = bool(absolute or season is None)
    label = f"E{episode}" if absolute else f"S{int(season):02d}E{int(episode):02d}"
    lang = str(metadata_language or "en").strip().lower()
    scope = "description_only" if str(metadata_language_scope or "all").lower() == "description_only" else "all"

    # 1) Kitsu (native absolute-episode support via ani.zip) — pass through as-is.
    try:
        result = await kitsu.fetch_anime_tv(
            title, season, episode, encoded_string,
            year=year, quality=quality, absolute=absolute,
        )
        if result:
            LOGGER.info(f"[ANIME] Kitsu hit for '{title}' {label}")
            return result
    except Exception as e:
        LOGGER.warning(f"[ANIME] Kitsu error for '{title}': {e}")

    # For absolute episodes without a mapped season, use season 1 + absolute number
    # so downstream providers and Stremio still get a valid S/E pair.
    use_season = 1 if absolute else int(season)
    use_episode = int(episode)

    # 2) TVDB (fully language-aware builder)
    try:
        result = await tvdb.fetch_series_metadata(
            title, use_season, use_episode, encoded_string,
            year=year, quality=quality,
            language=lang, language_scope=scope,
        )
        if result:
            if absolute:
                result["season_number"] = result.get("season_number") or use_season
                result["episode_number"] = use_episode
                result["absolute_episode"] = use_episode
            LOGGER.info(f"[ANIME] TVDB hit for '{title}' {label} [lang={lang} scope={scope}]")
            return result
    except Exception as e:
        LOGGER.warning(f"[ANIME] TVDB error for '{title}': {e}")

    # 3) TMDB
    try:
        hit = await tmdb.safe_search(title, "tv", year)
        if hit:
            payload = await _tmdb_tv_payload(
                hit.id, use_season, use_episode, quality, encoded_string,
                lang=lang, scope=scope,
            )
            if payload:
                LOGGER.info(f"[ANIME] TMDB hit for '{title}' {label} [lang={lang} scope={scope}]")
                if absolute:
                    payload["absolute_episode"] = use_episode
                    if not payload.get("episode_title") or payload["episode_title"].startswith("S"):
                        payload["episode_title"] = f"Episode {use_episode}"
                return payload
    except Exception as e:
        LOGGER.warning(f"[ANIME] TMDB error for '{title}': {e}")

    # 4) Cinemeta (least priority, always English)
    imdb_id = await cinemeta.safe_search(title, "tvSeries", year)
    if imdb_id:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "tvSeries")
            ep = {} if absolute else await cinemeta.cached_season(imdb_id, use_season, use_episode)
            if detail:
                LOGGER.info(f"[ANIME] Cinemeta hit for '{title}' {label}")
                payload = cinemeta.build_tv_payload(
                    detail, ep or {}, imdb_id, title, use_season, use_episode, quality, encoded_string
                )
                if absolute:
                    payload["absolute_episode"] = use_episode
                    if not (ep or {}).get("title"):
                        payload["episode_title"] = f"Episode {use_episode}"
                return payload
        except Exception as e:
            LOGGER.warning(f"[ANIME] Cinemeta error for '{title}': {e}")

    LOGGER.info(f"[ANIME] No metadata for '{title}' {label}")
    return None


async def resolve_anime_movie(
    title: str,
    encoded_string,
    year=None,
    quality=None,
    *,
    metadata_language: str = "en",
    metadata_language_scope: str = "all",
) -> Optional[dict]:
    lang = str(metadata_language or "en").strip().lower()
    scope = "description_only" if str(metadata_language_scope or "all").lower() == "description_only" else "all"

    # 1) Kitsu — kept as-is (no localization attempts per project scope).
    try:
        result = await kitsu.fetch_anime_movie(title, encoded_string, year=year, quality=quality)
        if result:
            LOGGER.info(f"[ANIME] Kitsu movie hit for '{title}'")
            return result
    except Exception as e:
        LOGGER.warning(f"[ANIME] Kitsu movie error for '{title}': {e}")

    # 2) TVDB
    try:
        result = await tvdb.fetch_movie_metadata(
            title, encoded_string, year=year, quality=quality,
            language=lang, language_scope=scope,
        )
        if result:
            LOGGER.info(f"[ANIME] TVDB movie hit for '{title}' [lang={lang} scope={scope}]")
            return result
    except Exception as e:
        LOGGER.warning(f"[ANIME] TVDB movie error for '{title}': {e}")

    # 3) TMDB
    try:
        hit = await tmdb.safe_search(title, "movie", year)
        if hit:
            payload = await _tmdb_movie_payload(
                hit.id, quality, encoded_string, lang=lang, scope=scope,
            )
            if payload:
                LOGGER.info(f"[ANIME] TMDB movie hit for '{title}' [lang={lang} scope={scope}]")
                return payload
    except Exception as e:
        LOGGER.warning(f"[ANIME] TMDB movie error for '{title}': {e}")

    # 4) Cinemeta (always English)
    imdb_id = await cinemeta.safe_search(title, "movie", year)
    if imdb_id:
        try:
            detail = await cinemeta.cached_detail(imdb_id, "movie")
            if detail:
                LOGGER.info(f"[ANIME] Cinemeta movie hit for '{title}'")
                return cinemeta.build_movie_payload(detail, imdb_id, title, quality, encoded_string)
        except Exception as e:
            LOGGER.warning(f"[ANIME] Cinemeta movie error for '{title}': {e}")

    LOGGER.info(f"[ANIME] No movie metadata for '{title}'")
    return None
