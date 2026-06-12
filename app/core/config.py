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
LOG_DIR = _project_path(os.getenv("APP_LOG_DIR"), PROJECT_ROOT / "logs")
REMOTE_PROFILE_DIR = _project_path(os.getenv("APP_REMOTE_PROFILE_DIR"), DATA_DIR / "profiles")

DEFAULT_PLATFORM = "toutiao"

# PostgreSQL-backed job store. The job store persists durable publish/login
# records (no TTL); see app/jobs/store.py. When PGSQL_DSN is empty the store
# falls back to an in-memory dict for local dev/tests.
PGSQL_HOST = os.getenv("PGSQL_HOST", "").strip()
PGSQL_PORT = int(os.getenv("PGSQL_PORT", "5432"))
PGSQL_DB = os.getenv("PGSQL_DB", "").strip()
PGSQL_USER = os.getenv("PGSQL_USER", "").strip()
PGSQL_PASSWORD = os.getenv("PGSQL_PASSWORD", "")
PGSQL_POOL_MIN = int(os.getenv("PGSQL_POOL_MIN", "1"))
PGSQL_POOL_MAX = int(os.getenv("PGSQL_POOL_MAX", "10"))


def _pg_dsn() -> str:
    if not (PGSQL_HOST and PGSQL_DB and PGSQL_USER):
        return ""
    from urllib.parse import quote

    user = quote(PGSQL_USER, safe="")  # user may contain '-' etc.
    pw = quote(PGSQL_PASSWORD, safe="")
    return f"postgresql://{user}:{pw}@{PGSQL_HOST}:{PGSQL_PORT}/{PGSQL_DB}"


PGSQL_DSN = _pg_dsn()

PUBLISH_API_TOKEN = os.getenv("PUBLISH_API_TOKEN", "").strip()
