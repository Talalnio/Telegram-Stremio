import io
import re
import zipfile
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

from Backend import __version__
from Backend.helper.settings_manager import SettingsManager
from Backend.helper.subtitles import language_label, provider_language_value, subtitle_ext
from Backend.logger import LOGGER

_TIMEOUT = httpx.Timeout(25.0, connect=12.0)
_USER_AGENT = f"Telegram-Stremio/{__version__}"
_SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}


def _preferred_languages() -> list[str]:
    langs = SettingsManager.current().subtitle_search_languages
    return langs or ["eng"]


def _configured_providers() -> list[str]:
    s = SettingsManager.current()
    out = []
    if s.subdl_api_key:
        out.append("subdl")
    if s.subsource_api_key:
        out.append("subsource")
    if s.opensubtitles_api_key:
        out.append("opensubtitles")
    return out


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)


def _media_basics(doc: dict, media_type: str, season: Optional[int] = None, episode: Optional[int] = None) -> dict:
    return {
        "media_type": "tv" if media_type in ("tv", "series") else "movie",
        "title": str(doc.get("title") or "").strip(),
        "year": int(doc.get("release_year") or 0) if str(doc.get("release_year") or "").strip() else None,
        "imdb_id": str(doc.get("imdb_id") or "").strip(),
        "tmdb_id": int(doc.get("tmdb_id") or 0) if str(doc.get("tmdb_id") or "").strip() else None,
        "season": int(season) if season not in (None, "") else None,
        "episode": int(episode) if episode not in (None, "") else None,
    }


def _normalize_imdb_for_opensubtitles(imdb_id: str | None) -> Optional[int]:
    raw = str(imdb_id or "").strip().lower()
    if not raw:
        return None
    raw = raw.removeprefix("tt")
    try:
        return int(raw)
    except ValueError:
        return None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _absolute_subdl_url(path: str | None) -> str:
    if not path:
        return ""
    if str(path).startswith("http://") or str(path).startswith("https://"):
        return str(path)
    return f"https://dl.subdl.com{path}"


def _subtitle_name_from_headers(response: httpx.Response, fallback: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return fallback


def _pick_zip_member(data: bytes, preferred_name: str = "", season: Optional[int] = None, episode: Optional[int] = None) -> tuple[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir() and subtitle_ext(m.filename) in _SUBTITLE_EXTS]
        if not members:
            raise ValueError("No subtitle file found inside the downloaded archive.")

        target = None
        wanted_tag = f"s{int(season):02d}e{int(episode):02d}" if season and episode else ""
        for member in members:
            name = member.filename.lower()
            if wanted_tag and wanted_tag in name:
                target = member
                break
        if target is None and preferred_name:
            for member in members:
                if preferred_name.lower() in member.filename.lower():
                    target = member
                    break
        if target is None:
            target = min(members, key=lambda item: len(item.filename))

        return target.filename.rsplit("/", 1)[-1], archive.read(target)


def _extract_episode_from_name(name: str, season: Optional[int]) -> Optional[int]:
    low = str(name or "").lower()
    patterns = []
    if season:
        patterns.extend([
            rf"s{season:02d}e(\d{{1,3}})",
            rf"s{season}e(\d{{1,3}})",
            rf"{season:02d}x(\d{{1,3}})",
            rf"{season}x(\d{{1,3}})",
            rf"season\D*{season}\D+episode\D*(\d{{1,3}})",
        ])
    patterns.extend([
        r"\be(\d{1,3})\b",
        r"\bep(?:isode)?\D*(\d{1,3})\b",
    ])
    for pattern in patterns:
        match = re.search(pattern, low)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                continue
    return None


def _pick_zip_members_for_season(data: bytes, season: int) -> list[tuple[int, str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir() and subtitle_ext(m.filename) in _SUBTITLE_EXTS]
        if not members:
            return []

        picked: dict[int, tuple[str, bytes]] = {}
        for member in members:
            episode = _extract_episode_from_name(member.filename, season)
            if not episode:
                continue
            current = picked.get(episode)
            if current is None or len(member.filename) < len(current[0]):
                picked[episode] = (member.filename.rsplit("/", 1)[-1], archive.read(member))

        return [(episode, name, content) for episode, (name, content) in sorted(picked.items(), key=lambda item: item[0])]


def _episode_match(text: str, season: int, episode: int) -> bool:
    low = str(text or "").lower()
    patterns = [
        rf"s{season:02d}e{episode:02d}",
        rf"s{season}e{episode}",
        rf"{season:02d}x{episode:02d}",
        rf"{season}x{episode}",
        rf"season\D*{season}\D+episode\D*{episode}",
        rf"\b{season:02d}{episode:02d}\b",
    ]
    return any(re.search(pattern, low) for pattern in patterns)


def _season_match(text: str, season: int) -> bool:
    low = str(text or "").lower()
    patterns = [
        rf"s{season:02d}",
        rf"s{season}",
        rf"season\D*{season}\b",
        rf"\b{season:02d}\b",
    ]
    return any(re.search(pattern, low) for pattern in patterns)


def _is_complete_season_result(text: str, season: Optional[int]) -> bool:
    low = str(text or "").lower()
    complete_keywords = (
        "complete",
        "season pack",
        "complete season",
        "all episodes",
        "full season",
        "pack",
        "batch",
    )
    if season and not _season_match(low, season):
        return False
    return any(keyword in low for keyword in complete_keywords)


def _quality_label(text: str) -> str:
    low = str(text or "").lower()
    for token in ("2160p", "1080p", "720p", "480p"):
        if token in low:
            return token.upper()
    if "4k" in low:
        return "4K"
    if "bluray" in low or "blu-ray" in low:
        return "BluRay"
    if "webrip" in low:
        return "WEBRip"
    if "web-dl" in low or "webdl" in low:
        return "WEB-DL"
    if "hdrip" in low:
        return "HDRip"
    return ""


def _result_kind(info: dict, text: str, season_value: Optional[int], episode_value: Optional[int]) -> str:
    if info["media_type"] != "tv":
        return "movie"
    if info.get("season") and not info.get("episode"):
        if _is_complete_season_result(text, info["season"]):
            return "season_pack"
        if season_value and season_value == info["season"] and not episode_value:
            return "season"
    if info.get("season") and info.get("episode"):
        if (season_value and season_value == info["season"] and episode_value and episode_value == info["episode"]) or _episode_match(text, info["season"], info["episode"]):
            return "episode"
    return "other"


def _subsource_pick_title(items: list[dict], title: str, year: Optional[int]) -> Optional[dict]:
    if not items:
        return None

    def _score(item: dict) -> tuple[float, int]:
        item_title = str(item.get("title") or item.get("fullName") or "")
        sim = _similarity(title, item_title)
        item_year = _coerce_int(item.get("releaseYear") or item.get("year")) or 0
        year_bonus = 1 if year and item_year == year else 0
        return (sim, year_bonus)

    return max(items, key=_score)


async def _search_subdl(info: dict) -> list[dict]:
    settings = SettingsManager.current()
    if not settings.subdl_api_key:
        return []

    params: dict[str, Any] = {
        "languages": ",".join(provider_language_value(code, "subdl") for code in _preferred_languages()),
        "type": "tv" if info["media_type"] == "tv" else "movie",
        "unpack": 1,
    }
    if info.get("imdb_id"):
        params["imdb_id"] = info["imdb_id"]
    elif info.get("tmdb_id"):
        params["tmdb_id"] = info["tmdb_id"]
    else:
        params["film_name"] = info["title"]
        if info.get("year"):
            params["year"] = info["year"]

    if info["media_type"] == "tv" and info.get("season"):
        params["season"] = info["season"]
        if info.get("episode"):
            params["episode"] = info["episode"]

    headers = {"Authorization": f"Bearer {settings.subdl_api_key}", "Accept": "application/json"}
    async with _http_client() as client:
        response = await client.get("https://api.subdl.com/api/v2/subtitles/search", params=params, headers=headers)
        if response.status_code != 200:
            LOGGER.warning(f"[Subtitles] SubDL search failed: HTTP {response.status_code}")
            return []
        payload = response.json()

    subtitles = payload.get("subtitles") or []
    results = []
    for entry in subtitles:
        base_nid = str(entry.get("n_id") or entry.get("nId") or entry.get("id") or "").strip()
        release = str(entry.get("release_name") or entry.get("name") or "SubDL subtitle").strip()
        if entry.get("unpack_files"):
            for unpack in entry.get("unpack_files") or []:
                file_name = str(unpack.get("name") or "")
                lang = str(unpack.get("language") or "").strip().lower()
                result_text = " ".join([file_name, release])
                season_value = _coerce_int(unpack.get("season"))
                episode_value = _coerce_int(unpack.get("episode"))
                match_kind = _result_kind(info, result_text, season_value, episode_value)
                if info["media_type"] == "tv" and info.get("season") and info.get("episode") and match_kind != "episode":
                    continue
                if info["media_type"] == "tv" and info.get("season") and not info.get("episode") and match_kind not in {"season_pack", "season"}:
                    continue
                results.append({
                    "provider": "subdl",
                    "provider_label": "SubDL",
                    "provider_id": base_nid,
                    "download_url": _absolute_subdl_url(unpack.get("url")),
                    "file_name": file_name or f"{release}{subtitle_ext(file_name or '.srt')}",
                    "release_name": release,
                    "source_line": "Source: SubDL",
                    "quality": _quality_label(result_text),
                    "match_kind": match_kind,
                    "lang_code": str(next((code for code in _preferred_languages() if provider_language_value(code, 'subdl') == lang), "und")),
                    "lang_label": language_label(next((code for code in _preferred_languages() if provider_language_value(code, 'subdl') == lang), "und")),
                    "hearing_impaired": bool(unpack.get("hi")),
                })
            continue

        file_name = str(entry.get("name") or release or "subtitle.srt")
        lang = str(entry.get("language") or "").strip().lower()
        result_text = " ".join([file_name, release])
        match_kind = _result_kind(info, result_text, None, None)
        if info["media_type"] == "tv" and info.get("season") and info.get("episode") and match_kind != "episode":
            continue
        if info["media_type"] == "tv" and info.get("season") and not info.get("episode") and match_kind not in {"season_pack", "season"}:
            continue
        results.append({
            "provider": "subdl",
            "provider_label": "SubDL",
            "provider_id": base_nid,
            "download_url": "",
            "file_name": file_name,
            "release_name": release,
            "source_line": "Source: SubDL",
            "quality": _quality_label(result_text),
            "match_kind": match_kind,
            "lang_code": str(next((code for code in _preferred_languages() if provider_language_value(code, 'subdl') == lang), "und")),
            "lang_label": language_label(next((code for code in _preferred_languages() if provider_language_value(code, 'subdl') == lang), "und")),
            "hearing_impaired": bool(entry.get("hi")),
        })

    return results


async def _search_subsource(info: dict) -> list[dict]:
    settings = SettingsManager.current()
    if not settings.subsource_api_key:
        return []

    headers = {
        "X-API-Key": settings.subsource_api_key,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }

    async with _http_client() as client:
        resp = await client.get(
            "https://api.subsource.net/api/v1/movies/search",
            params={"searchType": "text", "q": info["title"], **({"year": info["year"]} if info.get("year") else {})},
            headers=headers,
        )
        if resp.status_code != 200:
            LOGGER.warning(f"[Subtitles] Subsource title search failed: HTTP {resp.status_code}")
            return []
        items = (resp.json() or {}).get("data") or []
        picked = _subsource_pick_title(items, info["title"], info.get("year"))
        if not picked:
            return []
        movie_id = _coerce_int(picked.get("movieId") or picked.get("id"))
        if not movie_id:
            return []

        results = []
        for code in _preferred_languages():
            lang_name = provider_language_value(code, "subsource")
            sub_resp = await client.get(
                "https://api.subsource.net/api/v1/subtitles",
                params={"movieId": movie_id, "language": lang_name},
                headers=headers,
            )
            if sub_resp.status_code != 200:
                LOGGER.warning(f"[Subtitles] Subsource subtitles search failed for {lang_name}: HTTP {sub_resp.status_code}")
                continue
            entries = (sub_resp.json() or {}).get("data") or []
            for entry in entries:
                subtitle_id = str(entry.get("subtitleId") or entry.get("id") or "").strip()
                if not subtitle_id:
                    continue
                release = str(entry.get("releaseInfo") or entry.get("releaseName") or entry.get("fullLink") or entry.get("name") or "Subsource subtitle").strip()
                file_name = str(entry.get("name") or release or f"{info['title']}.srt").strip()
                haystack = " ".join(
                    str(entry.get(key) or "")
                    for key in ("releaseInfo", "releaseName", "fullLink", "name")
                )
                season_value = _coerce_int(entry.get("season"))
                episode_value = _coerce_int(entry.get("episode"))
                match_kind = _result_kind(info, haystack, season_value, episode_value)
                if info["media_type"] == "tv" and info.get("season") and info.get("episode") and match_kind != "episode":
                    continue
                if info["media_type"] == "tv" and info.get("season") and not info.get("episode") and match_kind not in {"season_pack", "season"}:
                    continue
                results.append({
                    "provider": "subsource",
                    "provider_label": "Subsource",
                    "provider_id": subtitle_id,
                    "download_url": "",
                    "file_name": file_name,
                    "release_name": release,
                    "source_line": "Source: Subsource",
                    "quality": _quality_label(haystack),
                    "match_kind": match_kind,
                    "lang_code": code,
                    "lang_label": language_label(code),
                    "hearing_impaired": bool(entry.get("hi")),
                })
        return results


async def _search_opensubtitles(info: dict) -> list[dict]:
    settings = SettingsManager.current()
    if not settings.opensubtitles_api_key:
        return []

    imdb_num = _normalize_imdb_for_opensubtitles(info.get("imdb_id"))
    params: dict[str, Any] = {
        "languages": ",".join(provider_language_value(code, "opensubtitles") for code in _preferred_languages()),
    }
    if info["media_type"] == "tv":
        if info.get("season"):
            params["season_number"] = info["season"]
        if info.get("episode"):
            params["type"] = "episode"
            params["episode_number"] = info["episode"]
        if imdb_num:
            params["parent_imdb_id"] = imdb_num
        elif info.get("tmdb_id"):
            params["parent_tmdb_id"] = info["tmdb_id"]
        else:
            params["query"] = info["title"]
    else:
        params["type"] = "movie"
        if imdb_num:
            params["imdb_id"] = imdb_num
        elif info.get("tmdb_id"):
            params["tmdb_id"] = info["tmdb_id"]
        else:
            params["query"] = info["title"]
            if info.get("year"):
                params["year"] = info["year"]

    headers = {
        "Api-Key": settings.opensubtitles_api_key,
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    async with _http_client() as client:
        resp = await client.get("https://api.opensubtitles.com/api/v1/subtitles", params=params, headers=headers)
        if resp.status_code != 200:
            LOGGER.warning(f"[Subtitles] OpenSubtitles search failed: HTTP {resp.status_code}")
            return []
        payload = resp.json() or {}

    results = []
    for entry in payload.get("data") or []:
        attrs = entry.get("attributes") or {}
        lang_code = str(attrs.get("language") or "").strip().lower()
        mapped_code = next((code for code in _preferred_languages() if provider_language_value(code, "opensubtitles") == lang_code), "und")
        for file_info in attrs.get("files") or []:
            result_text = " ".join([
                str(file_info.get("file_name") or ""),
                str(attrs.get("release") or ""),
                str(attrs.get("feature_details", {}).get("title") or ""),
            ])
            season_value = _coerce_int(attrs.get("feature_details", {}).get("season_number") or attrs.get("season_number"))
            episode_value = _coerce_int(attrs.get("feature_details", {}).get("episode_number") or attrs.get("episode_number"))
            match_kind = _result_kind(info, result_text, season_value, episode_value)
            if info["media_type"] == "tv" and info.get("season") and info.get("episode") and match_kind != "episode":
                continue
            if info["media_type"] == "tv" and info.get("season") and not info.get("episode") and match_kind not in {"season_pack", "season"}:
                continue
            results.append({
                "provider": "opensubtitles",
                "provider_label": "OpenSubtitles",
                "provider_id": str(attrs.get("subtitle_id") or entry.get("id") or "").strip(),
                "file_id": _coerce_int(file_info.get("file_id")),
                "download_url": "",
                "file_name": str(file_info.get("file_name") or f"{attrs.get('release') or 'subtitle'}.srt"),
                "release_name": str(attrs.get("release") or attrs.get("feature_details", {}).get("title") or "OpenSubtitles subtitle"),
                "source_line": "Source: OpenSubtitles",
                "quality": _quality_label(result_text),
                "match_kind": match_kind,
                "lang_code": mapped_code,
                "lang_label": language_label(mapped_code),
                "hearing_impaired": bool(attrs.get("hearing_impaired")),
                "download_count": _coerce_int(attrs.get("download_count")) or 0,
            })
    return results


def _result_sort_key(item: dict) -> tuple:
    provider_rank = {"subdl": 0, "opensubtitles": 1, "subsource": 2}.get(item.get("provider"), 9)
    match_rank = {"season_pack": 0, "season": 1, "episode": 2, "movie": 3, "other": 4}.get(item.get("match_kind"), 9)
    return (
        match_rank,
        provider_rank,
        0 if item.get("lang_code") in _preferred_languages() else 1,
        -(item.get("download_count") or 0),
        item.get("file_name") or item.get("release_name") or "",
    )


async def search_remote_subtitles(doc: dict, media_type: str, season: Optional[int] = None, episode: Optional[int] = None) -> dict:
    providers = _configured_providers()
    if not providers:
        return {"providers": [], "results": []}

    info = _media_basics(doc, media_type, season, episode)
    tasks = []
    if "subdl" in providers:
        tasks.append(_search_subdl(info))
    if "subsource" in providers:
        tasks.append(_search_subsource(info))
    if "opensubtitles" in providers:
        tasks.append(_search_opensubtitles(info))

    results = []
    for batch in await __import__("asyncio").gather(*tasks, return_exceptions=True):
        if isinstance(batch, Exception):
            LOGGER.warning(f"[Subtitles] provider search error: {batch}")
            continue
        results.extend(batch or [])

    deduped = []
    seen = set()
    for item in sorted(results, key=_result_sort_key):
        key = (item.get("provider"), item.get("provider_id"), item.get("file_name"), item.get("lang_code"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return {"providers": providers, "results": deduped}


def extract_downloaded_subtitles(filename: str, content: bytes, result: dict, season: Optional[int] = None, episode: Optional[int] = None) -> list[dict]:
    if not content:
        return []

    raw_name = str(filename or result.get("file_name") or "subtitle.srt")
    match_kind = str(result.get("match_kind") or "").strip().lower()
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return [{
            "name": raw_name,
            "content": content,
            "season": season if season else None,
            "episode": episode if episode else None,
        }]

    if season and not episode and match_kind == "season_pack":
        season_members = _pick_zip_members_for_season(content, season)
        if season_members:
            return [{
                "name": member_name,
                "content": member_bytes,
                "season": season,
                "episode": member_episode,
            } for member_episode, member_name, member_bytes in season_members]

    picked_name, picked_bytes = _pick_zip_member(content, str(result.get("file_name") or ""), season=season, episode=episode)
    return [{
        "name": picked_name,
        "content": picked_bytes,
        "season": season if season else None,
        "episode": episode if episode else None,
    }]


async def download_remote_subtitle(result: dict, season: Optional[int] = None, episode: Optional[int] = None) -> tuple[str, bytes]:
    provider = str(result.get("provider") or "").strip().lower()
    settings = SettingsManager.current()

    if provider == "subdl":
        headers = {"Authorization": f"Bearer {settings.subdl_api_key}", "Accept": "*/*"}
        download_url = str(result.get("download_url") or "").strip()
        async with _http_client() as client:
            if download_url:
                resp = await client.get(download_url, headers=headers)
            else:
                pid = str(result.get("provider_id") or "").strip()
                resp = await client.get(f"https://api.subdl.com/api/v2/subtitles/{pid}/download", params={"format": "file"}, headers=headers)
            resp.raise_for_status()
            return _subtitle_name_from_headers(resp, str(result.get("file_name") or "subtitle.srt")), resp.content

    if provider == "subsource":
        headers = {"X-API-Key": settings.subsource_api_key, "Accept": "*/*", "User-Agent": _USER_AGENT}
        pid = str(result.get("provider_id") or "").strip()
        async with _http_client() as client:
            resp = await client.get(f"https://api.subsource.net/api/v1/subtitles/{pid}/download", headers=headers)
            resp.raise_for_status()
            filename = _subtitle_name_from_headers(resp, str(result.get("file_name") or "subtitle.zip"))
            return filename, resp.content

    if provider == "opensubtitles":
        headers = {
            "Api-Key": settings.opensubtitles_api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        payload = {"file_id": _coerce_int(result.get("file_id"))}
        async with _http_client() as client:
            resp = await client.post("https://api.opensubtitles.com/api/v1/download", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            download_link = str(data.get("link") or "").strip()
            if not download_link:
                raise ValueError("OpenSubtitles did not return a download link.")
            file_resp = await client.get(download_link, headers={"User-Agent": _USER_AGENT})
            file_resp.raise_for_status()
            filename = str(data.get("file_name") or result.get("file_name") or "subtitle.srt")
            return filename, file_resp.content

    raise ValueError("Unsupported subtitle provider.")
