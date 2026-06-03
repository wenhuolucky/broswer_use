"""服务配置"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 服务配置
HOST = os.getenv("SERVICE_HOST", "127.0.0.1")
PORT = int(os.getenv("SERVICE_PORT", "8000"))
WORKERS = int(os.getenv("SERVICE_WORKERS", "1"))  # uvicorn workers

# 日志配置
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(request_id)-36s | %(module)s:%(lineno)d | %(message)s"

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# browser-use 相关
USER_DATA_DIR = PROJECT_ROOT / "chrome_profile"
EDGE_CDP_PORT = 9227

# 服务版本
SERVICE_VERSION = "1.0.0"
