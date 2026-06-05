from pathlib import Path


AUTO_ROOT = Path(__file__).resolve().parent
DATA_DIR = AUTO_ROOT / "data"
COOKIE_DIR = DATA_DIR / "cookies"
JOB_DIR = DATA_DIR / "jobs"
LOG_DIR = AUTO_ROOT / "logs"
REMOTE_PROFILE_DIR = DATA_DIR / "remote_profiles"

DEFAULT_PLATFORM = "toutiao"
REMOTE_LOGIN_TIMEOUT_SECONDS = 600
