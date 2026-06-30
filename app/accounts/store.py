from __future__ import annotations

from typing import Any

from app.accounts.models import ArticleAccount, STATUS_NORMAL
from app.core.config import (
    GROUP_ID,
    GROUP_TEXT,
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
)


class AccountStoreConfigError(RuntimeError):
    pass


def _require_account_store_config() -> None:
    missing = [
        name
        for name, value in {
            "MYSQL_HOST": MYSQL_HOST,
            "MYSQL_USER": MYSQL_USER,
            "MYSQL_DATABASE": MYSQL_DATABASE,
            "GROUP_ID": GROUP_ID,
        }.items()
        if not value
    ]
    if missing:
        raise AccountStoreConfigError(f"missing account store config: {', '.join(missing)}")


class AccountStore:
    """MySQL article_accounts store.

    表已存在，后端不创建、不迁移；所有方法都自动按 GROUP_ID 限定租户。
    """

    def __init__(self, *, group_id: str = GROUP_ID, group_text: str = GROUP_TEXT):
        self.group_id = group_id
        self.group_text = group_text or group_id
        _require_account_store_config()

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:
            raise AccountStoreConfigError("pymysql is required for account store") from exc
        return pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def ready(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.execute("SELECT 1 FROM article_accounts LIMIT 1")

    def list_accounts(
        self,
        *,
        platform: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArticleAccount]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clauses = ["group_id = %s"]
        params: list[Any] = [self.group_id]
        if platform:
            clauses.append("platform = %s")
            params.append(platform)
        params.extend([limit, offset])
        sql = (
            "SELECT id, phone, platform, channel, status, consecutive_failures, "
            "created_at, updated_at, group_id, group_text "
            f"FROM article_accounts WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [self._row_to_account(row) for row in cur.fetchall()]

    def count_accounts(self, *, platform: str | None = None) -> int:
        clauses = ["group_id = %s"]
        params: list[Any] = [self.group_id]
        if platform:
            clauses.append("platform = %s")
            params.append(platform)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS count FROM article_accounts WHERE {' AND '.join(clauses)}",
                    params,
                )
                row = cur.fetchone() or {}
                return int(row.get("count") or 0)

    def get_account(self, platform: str, phone: str) -> ArticleAccount | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone, platform, channel, status, consecutive_failures, "
                    "created_at, updated_at, group_id, group_text "
                    "FROM article_accounts WHERE group_id = %s AND platform = %s AND phone = %s LIMIT 1",
                    (self.group_id, platform, phone),
                )
                row = cur.fetchone()
        return self._row_to_account(row) if row else None

    def find_by_channel(self, channel: str) -> ArticleAccount | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, phone, platform, channel, status, consecutive_failures, "
                    "created_at, updated_at, group_id, group_text "
                    "FROM article_accounts WHERE group_id = %s AND channel = %s LIMIT 1",
                    (self.group_id, channel),
                )
                row = cur.fetchone()
        return self._row_to_account(row) if row else None

    def upsert_account(
        self,
        *,
        platform: str,
        phone: str,
        channel: str,
        status: str = STATUS_NORMAL,
        consecutive_failures: int = 0,
    ) -> ArticleAccount:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO article_accounts "
                    "(phone, platform, channel, status, consecutive_failures, group_id, group_text) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "channel = VALUES(channel), status = VALUES(status), "
                    "consecutive_failures = VALUES(consecutive_failures), group_text = VALUES(group_text)",
                    (
                        phone,
                        platform,
                        channel,
                        status,
                        int(consecutive_failures),
                        self.group_id,
                        self.group_text,
                    ),
                )
        account = self.get_account(platform, phone)
        if account is None:
            raise KeyError(f"account not found after upsert: {platform}/{phone}")
        return account

    def update_status(
        self,
        platform: str,
        phone: str,
        *,
        status: str,
        consecutive_failures: int | None = None,
    ) -> ArticleAccount:
        sets = ["status = %s", "group_text = %s"]
        params: list[Any] = [status, self.group_text]
        if consecutive_failures is not None:
            sets.append("consecutive_failures = %s")
            params.append(int(consecutive_failures))
        params.extend([self.group_id, platform, phone])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE article_accounts SET {', '.join(sets)} "
                    "WHERE group_id = %s AND platform = %s AND phone = %s",
                    params,
                )
                if cur.rowcount == 0:
                    raise KeyError(f"{platform}/{phone}")
        account = self.get_account(platform, phone)
        if account is None:
            raise KeyError(f"{platform}/{phone}")
        return account

    def rename_phone(self, platform: str, phone: str, new_phone: str) -> ArticleAccount:
        if self.get_account(platform, new_phone) is not None:
            raise ValueError("account_phone_exists")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE article_accounts SET phone = %s, group_text = %s "
                    "WHERE group_id = %s AND platform = %s AND phone = %s",
                    (new_phone, self.group_text, self.group_id, platform, phone),
                )
                if cur.rowcount == 0:
                    raise KeyError(f"{platform}/{phone}")
        account = self.get_account(platform, new_phone)
        if account is None:
            raise KeyError(f"{platform}/{new_phone}")
        return account

    def delete_account(self, platform: str, phone: str) -> ArticleAccount | None:
        account = self.get_account(platform, phone)
        if account is None:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM article_accounts WHERE group_id = %s AND platform = %s AND phone = %s",
                    (self.group_id, platform, phone),
                )
        return account

    def _row_to_account(self, row: dict[str, Any]) -> ArticleAccount:
        return ArticleAccount(
            id=row.get("id", ""),
            phone=str(row.get("phone", "") or ""),
            platform=str(row.get("platform", "") or ""),
            channel=str(row.get("channel", "") or ""),
            status=str(row.get("status", "") or STATUS_NORMAL),
            consecutive_failures=int(row.get("consecutive_failures") or 0),
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
            group_id=str(row.get("group_id", "") or self.group_id),
            group_text=str(row.get("group_text", "") or self.group_text),
        )
