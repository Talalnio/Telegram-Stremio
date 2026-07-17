from os import getenv, path, name as os_name
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(path.join(path.dirname(path.dirname(__file__)), "config.env"))

def get_default_library_path() -> str:
    if os_name == "nt":
        return str(Path.home() / "Stremio Library")
    elif os_name == "posix":
        return str(Path.home() / "Stremio Library")
    else:
        return "/app/library"

class Telegram:
    API_ID = int(getenv("API_ID", "0"))
    API_HASH = getenv("API_HASH", "")
    BOT_TOKEN = getenv("BOT_TOKEN", "")
    HELPER_BOT_TOKEN = getenv("HELPER_BOT_TOKEN", "")

    BASE_URL = getenv("BASE_URL", "").rstrip('/')
    PORT = int(getenv("PORT", "8000"))

    PARALLEL = int(getenv("PARALLEL", "1"))
    PRE_FETCH = int(getenv("PRE_FETCH", "1"))

    AUTH_CHANNEL = [channel.strip() for channel in (getenv("AUTH_CHANNEL") or "").split(",") if channel.strip()]
    DATABASE = [db.strip() for db in (getenv("DATABASE") or "").split(",") if db.strip()]

    TMDB_API = getenv("TMDB_API", "")

    UPSTREAM_REPO = getenv("UPSTREAM_REPO", "")
    UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "")

    OWNER_ID = int(getenv("OWNER_ID", "5422223708"))
    
    REPLACE_MODE = getenv("REPLACE_MODE", "true").lower() == "true"
    HIDE_CATALOG = getenv("HIDE_CATALOG", "false").lower() == "true"
    # SKIP_MULTIPART = getenv("SKIP_MULTIPART", "true").lower() == "true"

    ADMIN_USERNAME = getenv("ADMIN_USERNAME", "fyvio")
    ADMIN_PASSWORD = getenv("ADMIN_PASSWORD", "fyvio")
    
    SUBSCRIPTION = getenv("SUBSCRIPTION", "false").lower() == "true"
    SUBSCRIPTION_GROUP_ID = int(getenv("SUBSCRIPTION_GROUP_ID", "0"))
    SUBSCRIPTION_URL = getenv("SUBSCRIPTION_URL", "https://t.me/")
    APPROVER_IDS = [int(x.strip()) for x in (getenv("APPROVER_IDS") or "").split(",") if x.strip().isdigit()]

    LIBRARY_PATH = getenv("LIBRARY_PATH", "") or get_default_library_path()
    LIBRARY_TOKEN = getenv("LIBRARY_TOKEN", "")
    AUTO_SYNC_LIBRARY = getenv("AUTO_SYNC_LIBRARY", "false").lower() == "true"
    AUTO_SYNC_LIBRARY_DELAY = int(getenv("AUTO_SYNC_LIBRARY_DELAY", "5"))
    AUTO_SYNC_LIBRARY_INTERVAL_MIN = int(getenv("AUTO_SYNC_LIBRARY_INTERVAL_MIN", "0"))
    LIBRARY_PRUNE = getenv("LIBRARY_PRUNE", "true").lower() == "true"
    LIBRARY_PRUNE_EMPTY_DIRS = getenv("LIBRARY_PRUNE_EMPTY_DIRS", "true").lower() == "true"

    REQUESTS_ENABLED = getenv("REQUESTS_ENABLED", "false").lower() == "true"
    REQUESTS_CHANNEL_ID = int(getenv("REQUESTS_CHANNEL_ID", "0"))
    REQUESTS_INVITE_LINK = getenv("REQUESTS_INVITE_LINK", "")
    REQUESTS_ADMIN_IDS = [int(x.strip()) for x in (getenv("REQUESTS_ADMIN_IDS") or "").split(",") if x.strip().isdigit()]
    REQUESTS_MAX_RESULTS = int(getenv("REQUESTS_MAX_RESULTS", "10"))

    NOTIFY_ENABLED = getenv("NOTIFY_ENABLED", "false").lower() == "true"
    NOTIFY_CHANNEL_ID = int(getenv("NOTIFY_CHANNEL_ID", "0"))
    NOTIFY_INVITE_LINK = getenv("NOTIFY_INVITE_LINK", "")
    NOTIFY_POLL_SECONDS = int(getenv("NOTIFY_POLL_SECONDS", "60"))
    NOTIFY_AR_OVERVIEW = getenv("NOTIFY_AR_OVERVIEW", "false").lower() == "true"

    REQUIRED_CHANNEL_ID = int(getenv("REQUIRED_CHANNEL_ID", "0"))
    REQUIRED_INVITE_LINK = getenv("REQUIRED_INVITE_LINK", "")

    MULTI_CLIENT_START_MAX_CONCURRENCY = int(getenv("MULTI_CLIENT_START_MAX_CONCURRENCY", "1"))
    MULTI_CLIENT_START_GAP_SECONDS = int(getenv("MULTI_CLIENT_START_GAP_SECONDS", "2"))
    MULTI_CLIENT_FLOODWAIT_MAX_SCHEDULE = int(getenv("MULTI_CLIENT_FLOODWAIT_MAX_SCHEDULE", "86400"))
