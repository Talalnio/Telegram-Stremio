from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")

MediaType = Literal["movie", "series"]


# ══════════════════════════════════════════════════════════════
# §1 — UNIFIED ENVELOPES (response wrapper + error)
# ══════════════════════════════════════════════════════════════
class ApiSuccess(BaseModel, Generic[T]):
    status: Literal["success"] = "success"
    data: T


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    doc_url: Optional[str] = None
    retry_after: Optional[float] = None


class ApiError(BaseModel):
    status: Literal["error"] = "error"
    error: ApiErrorDetail


def ok(data: T) -> ApiSuccess[T]:
    return ApiSuccess(status="success", data=data)


def fail(
    code: str,
    message: str,
    *,
    status: int = 400,
    details: Optional[Dict[str, Any]] = None,
    retry_after: Optional[float] = None,
) -> tuple[ApiError, int]:
    return (
        ApiError(error=ApiErrorDetail(code=code, message=message, details=details, retry_after=retry_after)),
        status,
    )


# ══════════════════════════════════════════════════════════════
# §2 — PAGINATION
# ══════════════════════════════════════════════════════════════
class PaginationInfo(BaseModel):
    current_page: int
    total_pages: int
    total_items: int
    page_size: int
    has_next: bool
    has_prev: bool


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    pagination: PaginationInfo


# ══════════════════════════════════════════════════════════════
# §3 — AUTH
# ══════════════════════════════════════════════════════════════
class UserProfile(BaseModel):
    id: int
    email: Optional[str] = None
    display_name: str
    avatar_url: Optional[str] = None
    subscription_status: Literal["active", "expired", "trial", "inactive"]
    subscription_expires_at: Optional[datetime] = None
    is_admin: bool = False
    created_at: Optional[datetime] = None


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"


class AuthResponse(BaseModel):
    user: UserProfile
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"


class RegisterRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    display_name: Optional[str] = None
    telegram_user_id: Optional[int] = None


class EmailLoginRequest(BaseModel):
    method: Literal["email"] = "email"
    email: str
    password: str


class TelegramLoginPayload(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


class TelegramLoginRequest(BaseModel):
    method: Literal["telegram"] = "telegram"
    widget_payload: TelegramLoginPayload


class TokenLoginRequest(BaseModel):
    token: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ══════════════════════════════════════════════════════════════
# §4 — MEDIA SUMMARY (reused across catalog/detail/search)
# ══════════════════════════════════════════════════════════════
class MediaSummary(BaseModel):
    tmdb_id: int
    db_index: int
    media_type: MediaType
    title: str
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    quality_tags: List[Literal["4K", "HDR", "1080p", "720p"]] = Field(default_factory=list)
    is_anime: bool = False
    has_watch_progress: bool = False
    in_library: bool = False
    total_seasons: Optional[int] = None
    total_episodes: Optional[int] = None
    next_unwatched_ep: Optional[Dict[str, Any]] = None

    @field_validator("rating", mode="before")
    @classmethod
    def _round_rating(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            return round(float(v), 1)
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════
# §5 — DISCOVER HOME
# ══════════════════════════════════════════════════════════════
class HeroCarouselItem(BaseModel):
    tmdb_id: int
    db_index: int
    media_type: MediaType
    title: str
    tagline: Optional[str] = None
    backdrop: Optional[str] = None
    poster: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    runtime_minutes: Optional[int] = None
    overview_short: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    quality_tags: List[str] = Field(default_factory=list)
    has_trailer: bool = False


class CustomCatalogPreview(BaseModel):
    id: str
    name: str
    poster: Optional[str] = None
    items_count: int
    items: List[MediaSummary] = Field(default_factory=list)


class DiscoverHome(BaseModel):
    hero_carousel: List[HeroCarouselItem]
    trending_now: List[MediaSummary]
    top_rated: List[MediaSummary]
    latest_movies: List[MediaSummary]
    latest_shows: List[MediaSummary]
    continue_watching: List[MediaSummary]
    my_list: List[MediaSummary]
    custom_catalogs: List[CustomCatalogPreview]


# ══════════════════════════════════════════════════════════════
# §6 — MEDIA DETAIL
# ══════════════════════════════════════════════════════════════
class EpisodeSummary(BaseModel):
    episode_number: int
    title: str
    overview: Optional[str] = None
    still: Optional[str] = None
    released: Optional[str] = None
    runtime_seconds: Optional[int] = None
    rating: Optional[float] = None
    has_stream: bool = False
    watch_progress: Optional[Dict[str, Any]] = None


class SeasonSummary(BaseModel):
    season_number: int
    name: str
    overview: Optional[str] = None
    poster: Optional[str] = None
    episode_count: int
    episodes: List[EpisodeSummary] = Field(default_factory=list)


class UserMediaState(BaseModel):
    in_library: bool = False
    favorite: bool = False
    user_rating: Optional[float] = None
    last_watched_episode: Optional[Dict[str, Any]] = None


class MediaDetail(BaseModel):
    tmdb_id: int
    db_index: int
    media_type: MediaType
    imdb_id: Optional[str] = None
    title: str
    tagline: Optional[str] = None
    poster: Optional[str] = None
    backdrop: Optional[str] = None
    logo: Optional[str] = None
    rating: Optional[float] = None
    release_year: Optional[int] = None
    runtime_minutes: Optional[int] = None
    total_seasons: Optional[int] = None
    total_episodes: Optional[int] = None
    overview: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    cast: List[str] = Field(default_factory=list)
    studios: List[str] = Field(default_factory=list)
    origin_country: List[str] = Field(default_factory=list)
    original_language: Optional[str] = None
    is_anime: bool = False
    quality_tags: List[str] = Field(default_factory=list)
    trailer_youtube_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    seasons: List[SeasonSummary] = Field(default_factory=list)
    user_state: Optional[UserMediaState] = None
    subtitle_languages_available: List[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# §7 — PLAYBACK
# ══════════════════════════════════════════════════════════════
class PlaybackSource(BaseModel):
    id: str
    label: str
    resolution: Optional[str] = None
    codec: Optional[str] = None
    size_bytes: Optional[int] = None
    size_readable: Optional[str] = None
    storage: str = "Telegram"
    audio_languages: List[str] = Field(default_factory=list)
    priority: int = 0
    stream_url: str


class PlaybackSubtitle(BaseModel):
    id: str
    label: str
    lang_code: str
    default: bool = False
    forced: bool = False
    format: Literal["vtt", "srt", "ass"] = "vtt"
    url: str


class PlaybackMeta(BaseModel):
    title: str
    full_title: Optional[str] = None
    show_title: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    media_type: MediaType
    backdrop: Optional[str] = None
    runtime_seconds: Optional[int] = None
    next_episode: Optional[Dict[str, Any]] = None
    prev_episode: Optional[Dict[str, Any]] = None
    intro_skip_range: Optional[list[int]] = None
    credits_start: Optional[int] = None


class PlaybackDefaults(BaseModel):
    preferred_quality_id: Optional[str] = None
    preferred_subtitle_id: Optional[str] = None
    auto_select_best_quality: bool = True
    auto_play_next: bool = True


class PlaybackManifest(BaseModel):
    meta: PlaybackMeta
    sources: List[PlaybackSource]
    subtitles: List[PlaybackSubtitle]
    user_defaults: PlaybackDefaults


class ProgressReportRequest(BaseModel):
    media_type: MediaType
    tmdb_id: int
    db_index: int
    season: Optional[int] = None
    episode: Optional[int] = None
    source_id: Optional[str] = None
    current_time: float = 0.0
    total_time: Optional[float] = None
    is_paused: bool = False
    volume_percent: Optional[int] = None
    bandwidth_mbps: Optional[float] = None


# ══════════════════════════════════════════════════════════════
# §8 — SETTINGS
# ══════════════════════════════════════════════════════════════
class SubtitleStyle(BaseModel):
    font_size: Literal["small", "medium", "large"] = "medium"
    background: Literal["none", "semi", "solid"] = "semi"
    text_color: str = "#FFFFFF"


class PlaybackSettings(BaseModel):
    default_quality: str = "auto"
    default_subtitle: str = "ar"
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    auto_play_next_episode: bool = True
    skip_intro_auto: bool = True
    skip_credits_auto: bool = False
    playback_speed: float = 1.0


class StremioTokenIntegration(BaseModel):
    configured: bool = False
    token_masked: Optional[str] = None
    added_at: Optional[datetime] = None
    addon_manifest_url: Optional[str] = None


class PATEntry(BaseModel):
    id: str
    name: str
    token_prefix: Optional[str] = None
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    limits: Dict[str, Any] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)


class Integrations(BaseModel):
    stremio_token: StremioTokenIntegration = Field(default_factory=StremioTokenIntegration)
    personal_access_tokens: List[PATEntry] = Field(default_factory=list)
    debrid_services: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class Appearance(BaseModel):
    theme: Literal["dark", "pitch_black"] = "pitch_black"
    accent_color: Literal["purple", "blue", "emerald", "rose"] = "purple"
    poster_density: Literal["compact", "default", "large"] = "default"


class Notifications(BaseModel):
    push_browser: bool = True
    email_new_episodes: bool = True
    telegram_new_episodes: bool = True
    telegram_updates: bool = True


class UserPlan(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    price_usd: Optional[float] = None
    days: Optional[int] = None


class UsageLimits(BaseModel):
    streams_concurrent: int = 2
    daily_gb: Optional[float] = None
    monthly_gb: Optional[float] = None


class SubscriptionSettings(BaseModel):
    status: Literal["active", "expired", "trial", "inactive"] = "inactive"
    plan: Optional[UserPlan] = None
    expires_at: Optional[datetime] = None
    limits: UsageLimits = Field(default_factory=UsageLimits)
    usage_current_period: Dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    device: Optional[str] = None
    ip: Optional[str] = None
    country: Optional[str] = None
    last_active_at: Optional[datetime] = None
    is_current: bool = False


class UserFullSettings(BaseModel):
    profile: UserProfile
    playback: PlaybackSettings = Field(default_factory=PlaybackSettings)
    integrations: Integrations = Field(default_factory=Integrations)
    appearance: Appearance = Field(default_factory=Appearance)
    notifications: Notifications = Field(default_factory=Notifications)
    subscription: SubscriptionSettings = Field(default_factory=SubscriptionSettings)
    sessions: List[Session] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# §9 — LIBRARY & SEARCH
# ══════════════════════════════════════════════════════════════
class LibraryAddRequest(BaseModel):
    tmdb_id: int
    db_index: int
    media_type: MediaType


class SearchSuggestion(BaseModel):
    label: str
    kind: Literal["movie", "series", "actor", "catalog"]
    id: str
    poster: Optional[str] = None


# ══════════════════════════════════════════════════════════════
# §10 — CATALOG FILTERING RESPONSE
# ══════════════════════════════════════════════════════════════
class FilterOptions(BaseModel):
    genres: List[str] = Field(default_factory=list)
    decades: List[str] = Field(default_factory=list)


class CatalogResponse(PaginatedResponse[MediaSummary]):
    available_filters: FilterOptions = Field(default_factory=FilterOptions)
