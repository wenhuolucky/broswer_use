import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

HOST = os.getenv("SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("SERVICE_PORT", "19000"))
WORKERS = int(os.getenv("SERVICE_WORKERS", "1"))

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("APP_DATA_DIR") or PROJECT_ROOT / "data")
LOG_DIR = Path(os.getenv("APP_LOG_DIR") or PROJECT_ROOT / "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(request_id)-36s | %(module)s:%(lineno)d | %(message)s"

USER_DATA_DIR = DATA_DIR / "chrome_profile"
EDGE_CDP_PORT = int(os.getenv("EDGE_CDP_PORT", "9227"))

SERVICE_VERSION = "1.0.0"
