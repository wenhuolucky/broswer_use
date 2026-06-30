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

# SQLite-backed job/channel store. 单节点自部署：作业与渠道持久化用一个文件型
# SQLite（取代 PostgreSQL），持久记录无 TTL（见 app/jobs/store.py）。SQLITE_PATH
# 留空则回退到内存 dict（仅本地开发/测试，重启即丢）。
_sqlite_raw = os.getenv("SQLITE_PATH", "").strip()
SQLITE_PATH = str(_project_path(_sqlite_raw, DATA_DIR / "app.db")) if _sqlite_raw else ""

PUBLISH_API_TOKEN = os.getenv("PUBLISH_API_TOKEN", "").strip()

# MySQL-backed article account store. The table is owned by operations / DBA
# and must already exist; the application only validates configuration and uses
# it through app.accounts.store.AccountStore.
MYSQL_HOST = os.getenv("MYSQL_HOST", "").strip()
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "").strip()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "").strip()
GROUP_ID = os.getenv("GROUP_ID", "").strip()
GROUP_TEXT = os.getenv("GROUP_TEXT", "").strip()

# 僵尸 pending 渠道清理：pending 渠道只在一次登录会话进行中存在，用户放弃登录
# （关页面/超时）会留下无 cookie 的空渠道。运行期无其他 GC（cancel/回收巡检都不删
# 渠道），故按 TTL 周期清扫：删除创建超过 TTL 仍未绑定的 pending 渠道。
# TTL 取值需远大于真实登录耗时，避免误删正在登录中的渠道（默认 30 分钟）。
PENDING_CHANNEL_TTL_SECONDS = float(os.getenv("PENDING_CHANNEL_TTL_SECONDS", "1800"))
PENDING_CHANNEL_SWEEP_INTERVAL_SECONDS = float(
    os.getenv("PENDING_CHANNEL_SWEEP_INTERVAL_SECONDS", "600")
)

# ── 多 IP 代理模块 ────────────────────────────────────────────────
# 每个渠道（channel）绑定一个独立静态代理 IP，登录和发文都通过同一 IP 出口，
# 避免多账号共用本机 IP 被平台识别为工作室触发风控。
# PROXY_ENABLED=false（默认）时全部直连，行为与未集成代理时完全一致。
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() in ("true", "1", "yes")
# 代理配置文件（ip_pool + defaults），默认项目根目录 proxies.yaml
PROXY_CONFIG_PATH = str(_project_path(os.getenv("PROXY_CONFIG_PATH"), PROJECT_ROOT / "proxies.yaml"))
# 渠道→IP 绑定关系持久化路径，默认 data/proxy_assignments.json
PROXY_ASSIGNMENTS_PATH = str(
    _project_path(os.getenv("PROXY_ASSIGNMENTS_PATH"), DATA_DIR / "proxy_assignments.json")
)
# IP 健康检查：连续失败阈值（默认 3 次）
PROXY_FAILURE_THRESHOLD = int(os.getenv("PROXY_FAILURE_THRESHOLD", "3"))
# IP 健康检查循环：是否启用定期巡检（默认 false）
PROXY_HEALTH_CHECK_ENABLED = os.getenv("PROXY_HEALTH_CHECK_ENABLED", "false").lower() in ("true", "1", "yes")
# IP 健康检查循环间隔（秒，默认 300 秒 = 5 分钟）
PROXY_HEALTH_CHECK_INTERVAL = int(os.getenv("PROXY_HEALTH_CHECK_INTERVAL", "300"))
