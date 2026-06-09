import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parents[1]


def _project_path(value: str | os.PathLike | None, default: Path) -> Path:
    path = Path(value) if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATA_DIR = _project_path(os.getenv("APP_DATA_DIR"), PROJECT_ROOT / "data")
COOKIE_DIR = DATA_DIR / "cookies"
JOB_DIR = DATA_DIR / "jobs"
LOG_DIR = _project_path(os.getenv("APP_LOG_DIR"), PROJECT_ROOT / "logs")
REMOTE_PROFILE_DIR = _project_path(os.getenv("APP_REMOTE_PROFILE_DIR"), DATA_DIR / "remote_profiles")

DEFAULT_PLATFORM = "toutiao"
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "browser_use:publish").strip() or "browser_use:publish"
REDIS_JOB_TTL_SECONDS = int(os.getenv("REDIS_JOB_TTL_SECONDS", "86400"))

PUBLISH_API_TOKEN = os.getenv("PUBLISH_API_TOKEN", "").strip()
