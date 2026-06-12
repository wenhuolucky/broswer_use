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


def _env_int(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


# Remote login VNC connection stability and viewer tweaks.
# These only affect the remote-login VNC link (app/api/vnc_proxy.py,
# app/remote/login.py); the publish flow and its CDP screencast viewer
# (app/remote/viewer.py) are untouched.
VNC_PROXY_KEEPALIVE_INTERVAL = _env_int("VNC_PROXY_KEEPALIVE_INTERVAL", 10)
# Upstream (proxy<->KasmVNC) WebSocket protocol ping. DISABLED by default (0).
# KasmVNC's websockify does NOT answer WebSocket protocol PING frames
# (novnc/websockify#170), so enabling library auto-ping makes websockets kill a
# perfectly live connection after interval+timeout seconds ("keepalive ping
# timeout"). The upstream hop is a local loopback (127.0.0.1) carrying a
# continuous screencast stream, so TCP + real traffic already prove liveness;
# a genuine KasmVNC crash drops TCP and surfaces via the bridge read loop.
# Set a positive interval only if you knowingly run a websockify that pongs.
KASMVNC_WS_PING_INTERVAL = _env_int("KASMVNC_WS_PING_INTERVAL", 0, min_value=0)
KASMVNC_WS_PING_TIMEOUT = _env_int("KASMVNC_WS_PING_TIMEOUT", 60, min_value=0)
# Maps to cloudflared `tunnel --proxy-keepalive-timeout` (idle connection
# close timeout for the cloudflared<->local-viewer hop; default upstream is
# 1m30s). Empty value disables the flag and keeps cloudflared defaults.
CLOUDFLARED_PROXY_KEEPALIVE_TIMEOUT = os.getenv("CLOUDFLARED_PROXY_KEEPALIVE_TIMEOUT", "3600s").strip()
VNC_HIDE_TOOLBAR = _env_bool("VNC_HIDE_TOOLBAR", True)

# Detailed browser-use agent diagnostics. These only affect logging volume and
# do not change publish behavior.
AGENT_VERBOSE_LOG_ENABLED = _env_bool("AGENT_VERBOSE_LOG_ENABLED", True)
AGENT_VERBOSE_LOG_MAX_CHARS = _env_int("AGENT_VERBOSE_LOG_MAX_CHARS", 12000)
AGENT_VERBOSE_LOG_MESSAGE_MAX_CHARS = _env_int("AGENT_VERBOSE_LOG_MESSAGE_MAX_CHARS", 12000)
