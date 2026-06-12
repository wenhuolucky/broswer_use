import os

# Path/env primitives live in app.core.config (single source of truth; it also
# runs load_dotenv on import). Re-use them here instead of redefining.
from app.core.config import DATA_DIR, LOG_DIR

HOST = os.getenv("SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("SERVICE_PORT", "8833"))
WORKERS = int(os.getenv("SERVICE_WORKERS", "1"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(request_id)-36s | %(module)s:%(lineno)d | %(message)s"

USER_DATA_DIR = DATA_DIR / "chrome_profile"

SERVICE_VERSION = "1.0.0"
