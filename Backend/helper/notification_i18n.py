from __future__ import annotations

import re
from typing import Dict, Optional

import httpx

_LABELS: Dict[str, Dict[str, str]] = {
    "EN": {
        "event_add": "📥 New Add",
        "event_remove": "❌ Removed",
        "quality": "💿 Quality:",
        "file_size": "📦 File Size:",
        "genres": "🎭 Genres:",
        "rating": "⭐️ Rating:",
        "episode_rating": "⭐️ Episode Rating:",
        "story": "📖 Story:",
        "season": "Season",
        "episode": "Episode",
        "tv_add": "📥 New Add",
    },
    "AR": {
        "event_add": "📥 إضافة جديدة",
        "event_remove": "❌ تمت الإزالة",
        "quality": "💿 الجودة:",
        "file_size": "📦 حجم الملف:",
        "genres": "🎭 التصنيفات:",
        "rating": "⭐️ التقييم:",
        "episode_rating": "⭐️ تقييم الحلقة:",
        "story": "📖 القصة:",
        "season": "الموسم",
        "episode": "الحلقة",
        "tv_add": "📥 إضافة جديدة",
    },
    "IN": {
        "event_add": "📥 नई जोड़ी गई",
        "event_remove": "❌ हटा दिया गया",
        "quality": "💿 गुणवत्ता:",
        "file_size": "📦 फ़ाइल का आकार:",
        "genres": "🎭 श्रेणियाँ:",
        "rating": "⭐️ रेटिंग:",
        "episode_rating": "⭐️ एपिसोड रेटिंग:",
        "story": "📖 कहानी:",
        "season": "सीज़न",
        "episode": "एपिसोड",
        "tv_add": "📥 नई जोड़ी गई",
    },
}

_GENRE_MAP: Dict[str, Dict[str, str]] = {
    "AR": {
        "Action": "أكشن",
        "Adventure": "مغامرات",
        "Animation": "أنيميشن",
        "Comedy": "كوميديا",
        "Crime": "جريمة",
        "Documentary": "وثائقي",
        "Drama": "دراما",
        "Family": "عائلي",
        "Fantasy": "خيال",
        "History": "تاريخي",
        "Horror": "رعب",
        "Music": "موسيقى",
        "Mystery": "غموض",
        "Romance": "رومانسي",
        "Science Fiction": "خيال علمي",
        "Sci-Fi & Fantasy": "خيال علمي وخيالي",
        "TV Movie": "فيلم تلفزيوني",
        "Thriller": "إثارة",
        "War": "حرب",
        "Western": "ويسترن",
        "Action & Adventure": "أكشن ومغامرات",
        "Kids": "أطفال",
        "News": "أخبار",
        "Reality": "واقعي",
        "Soap": "دراما اجتماعية",
        "Talk": "حوارات",
        "War & Politics": "حرب وسياسة",
        "Anime": "أنمي",
    },
    "IN": {
        "Action": "एक्शन",
        "Adventure": "साहसिक",
        "Animation": "एनिमेशन",
        "Comedy": "कॉमेडी",
        "Crime": "अपराध",
        "Documentary": "वृत्तचित्र",
        "Drama": "ड्रामा",
        "Family": "परिवार",
        "Fantasy": "काल्पनिक",
        "History": "ऐतिहासिक",
        "Horror": "हॉरर",
        "Music": "संगीत",
        "Mystery": "रहस्यमय",
        "Romance": "रोमांस",
        "Science Fiction": "विज्ञान कथा",
        "Sci-Fi & Fantasy": "साइ-फाई और फैंटेसी",
        "TV Movie": "टीवी मूवी",
        "Thriller": "थ्रिलर",
        "War": "युद्ध",
        "Western": "पश्चिमी",
        "Action & Adventure": "एक्शन और साहसिक",
        "Kids": "बच्चे",
        "News": "समाचार",
        "Reality": "रियलिटी",
        "Soap": "सोप",
        "Talk": "टॉक शो",
        "War & Politics": "युद्ध और राजनीति",
        "Anime": "एनीमे",
    },
}

SUPPORTED_LANGUAGES = ("EN", "AR", "IN")


def normalize_lang(lang: Optional[str]) -> str:
    if not lang:
        return "EN"
    v = str(lang).strip().upper()
    return v if v in SUPPORTED_LANGUAGES else "EN"


def label(key: str, lang: Optional[str]) -> str:
    lang_code = normalize_lang(lang)
    table = _LABELS.get(lang_code) or _LABELS["EN"]
    return table.get(key, _LABELS["EN"].get(key, key))


def localize_genre(genre: str, lang: Optional[str]) -> str:
    if not genre:
        return genre
    lang_code = normalize_lang(lang)
    if lang_code == "EN":
        return genre
    table = _GENRE_MAP.get(lang_code) or {}
    g = str(genre).strip()
    return table.get(g) or table.get(g.lower().title()) or g


def localize_genres(genres, lang: Optional[str]):
    if not genres:
        return []
    out = []
    for g in genres:
        if g:
            out.append(localize_genre(g, lang))
    return out


# ====================================================================
# Google Translate fallback for story/overview
# ====================================================================

_GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"

# Language codes for Google Translate
_GT_LANG_MAP = {
    "EN": "en",
    "AR": "ar",
    "IN": "hi",  # Hindi
}


async def _translate_text(text: str, target_lang: str) -> str:
    """Translate text using Google Translate free API."""
    if not text or not text.strip():
        return text

    target = _GT_LANG_MAP.get(target_lang, "en")
    if target == "en":
        # No need to translate if target is English and text is likely English
        return text

    try:
        params = {
            "client": "gtx",
            "sl": "auto",  # auto-detect source language
            "tl": target,
            "dt": "t",
            "q": text[:5000],  # Google Translate limit
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_GOOGLE_TRANSLATE_URL, params=params)
            if resp.status_code == 200:
                data = resp.json()
                # Parse Google Translate response format
                if data and isinstance(data[0], list):
                    translated_parts = []
                    for part in data[0]:
                        if isinstance(part, list) and len(part) > 0:
                            translated_parts.append(part[0])
                    if translated_parts:
                        return "".join(translated_parts)
    except Exception as e:
        # Silently fall back to original text on any error
        pass

    return text


def _is_likely_english(text: str) -> bool:
    """Quick heuristic to check if text is likely English."""
    if not text:
        return True
    # Count ASCII characters
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    total_chars = len(text.strip())
    if total_chars == 0:
        return True
    return (ascii_chars / total_chars) > 0.8


async def translate_story_if_needed(story: str, lang: Optional[str]) -> str:
    """
    Translate story/overview to target language if needed.
    Uses Google Translate as fallback when:
    - Target is not English (AR or IN)
    - Story appears to be in English
    """
    if not story or not story.strip():
        return story

    lang_code = normalize_lang(lang)

    # If target is English, return as-is
    if lang_code == "EN":
        return story

    # If story doesn't look like English, assume it's already in target language
    if not _is_likely_english(story):
        return story

    # Translate using Google Translate
    return await _translate_text(story, lang_code)
