from __future__ import annotations

import os
from threading import RLock
from typing import Any

from app.accounts.crypto import PhoneCrypto, normalize_phone
from app.accounts.errors import AccountConflict, AccountStoreUnavailable
from app.accounts.models import ArticleAccount, UNAVAILABLE_STATUSES

_SELECT_COLUMNS = (
    "id, phone, platform, channel, status, consecutive_failures, "
    "created_at, updated_at, group_id, group_text"
)


class MySQLAccountStore:
    """MySQL backed article_accounts store.

    表由部署侧提前创建；这里不做 migrate，只做读写。
    phone 字段存储 AES-SIV 确定性加密密文，同手机号同密钥密文相同，
    可直接用于 WHERE phone = ? 查询。
    """

    def __init__(self):
        self._lock = RLock()
        self._crypto: PhoneCrypto | None = None

    def _get_crypto(self) -> PhoneCrypto:
        """懒加载加密模块，首次调用时初始化。"""
        if self._crypto is None:
            self._crypto = PhoneCrypto()
        return self._crypto

    def ready(self) -> bool:
        self._require_config()
        # 初始化加密模块，密钥缺失时在此报错
        self._get_crypto()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return True

    def list_accounts(
        self,
        *,
        group_id: str,
        platform: str | None = None,
        status: str | None = None,
        available_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArticleAccount]:
        clauses = ["group_id = %s"]
        params: list[Any] = [group_id]
        if platform:
            clauses.append("platform = %s")
            params.append(platform)
        if status:
            clauses.append("status = %s")
            params.append(status)
        if available_only:
            clauses.append("status NOT IN (%s, %s)")
            params.extend(sorted(UNAVAILABLE_STATUSES))
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        params.extend([limit, offset])
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM article_accounts "
            f"WHERE {' AND '.join(clauses)} ORDER BY id ASC LIMIT %s OFFSET %s"
        )
        with self._locked_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_account(self, *, group_id: str, platform: str, phone: str) -> ArticleAccount | None:
        crypto = self._get_crypto()
        encrypted_phone = crypto.encrypt_phone(normalize_phone(phone))
        with self._locked_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM article_accounts "
                    "WHERE group_id = %s AND platform = %s AND phone = %s LIMIT 1",
                    (group_id, platform, encrypted_phone),
                )
                row = cur.fetchone()
        return self._row_to_account(row) if row else None

    def upsert_account(
        self,
        *,
        group_id: str,
        group_text: str,
        platform: str,
        phone: str,
        channel: str,
        status: str,
        consecutive_failures: int,
        reset_failures: bool,
    ) -> tuple[ArticleAccount, str | None]:
        """保存或重新绑定账号。

        Returns:
            (account, old_channel_id) — old_channel_id 为被替换的旧 channel（与入参不同），
            调用方应清理旧 channel 的关联资源（cookie、代理绑定等）。
            新增或 channel 未变化时 old_channel_id 为 None。
        """
        crypto = self._get_crypto()
        encrypted_phone = crypto.encrypt_phone(normalize_phone(phone))
        failures = 0 if reset_failures else max(0, int(consecutive_failures))
        old_channel: str | None = None
        with self._locked_connection() as conn:
            with conn.cursor() as cur:
                # 先查旧 channel，用于通知上层清理
                cur.execute(
                    f"SELECT channel FROM article_accounts "
                    "WHERE group_id = %s AND platform = %s AND phone = %s LIMIT 1",
                    (group_id, platform, encrypted_phone),
                )
                existing_row = cur.fetchone()
                if existing_row and str(existing_row["channel"] or "") != channel:
                    old_channel = str(existing_row["channel"])

                cur.execute(
                    """
                    INSERT INTO article_accounts
                        (
                            group_id, group_text, platform, phone, channel, status,
                            consecutive_failures, created_at, updated_at
                        )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                        group_text = VALUES(group_text),
                        channel = VALUES(channel),
                        status = VALUES(status),
                        consecutive_failures = VALUES(consecutive_failures),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (group_id, group_text, platform, encrypted_phone, channel, status, failures),
                )
        account = self.get_account(group_id=group_id, platform=platform, phone=phone)
        if account is None:
            raise AccountStoreUnavailable("保存账号后未能读取账号记录")
        return account, old_channel

    def update_account(
        self,
        *,
        group_id: str,
        platform: str,
        phone: str,
        group_text: str | None = None,
        new_phone: str | None = None,
        status: str | None = None,
        reset_failures: bool | None = None,
        consecutive_failures: int | None = None,
    ) -> ArticleAccount | None:
        crypto = self._get_crypto()
        current = self.get_account(group_id=group_id, platform=platform, phone=phone)
        if current is None:
            return None

        sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if group_text is not None:
            sets.append("group_text = %s")
            params.append(group_text)
        if new_phone:
            normalized_new = normalize_phone(new_phone)
            existing = self.get_account(group_id=group_id, platform=platform, phone=normalized_new)
            if existing is not None:
                raise AccountConflict("account_phone_exists", "当前分组下该手机号已存在")
            sets.append("phone = %s")
            params.append(crypto.encrypt_phone(normalized_new))
        if status is not None:
            sets.append("status = %s")
            params.append(status)
        if reset_failures is True:
            sets.append("consecutive_failures = %s")
            params.append(0)
        elif consecutive_failures is not None:
            sets.append("consecutive_failures = %s")
            params.append(max(0, int(consecutive_failures)))

        encrypted_old = crypto.encrypt_phone(normalize_phone(phone))
        params.extend([group_id, platform, encrypted_old])

        with self._locked_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE article_accounts SET {', '.join(sets)} "
                    "WHERE group_id = %s AND platform = %s AND phone = %s",
                    params,
                )
        return self.get_account(group_id=group_id, platform=platform, phone=new_phone or phone)

    def delete_account(self, *, group_id: str, platform: str, phone: str) -> ArticleAccount | None:
        crypto = self._get_crypto()
        account = self.get_account(group_id=group_id, platform=platform, phone=phone)
        if account is None:
            return None
        encrypted_phone = crypto.encrypt_phone(normalize_phone(phone))
        with self._locked_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM article_accounts WHERE group_id = %s AND platform = %s AND phone = %s",
                    (group_id, platform, encrypted_phone),
                )
        return account

    def _require_config(self) -> None:
        host, _port, user, _password, database = self._settings()
        missing = [
            name
            for name, value in (
                ("MYSQL_HOST", host),
                ("MYSQL_USER", user),
                ("MYSQL_DATABASE", database),
            )
            if not value
        ]
        if missing:
            raise AccountStoreUnavailable(f"缺少账号 MySQL 配置: {', '.join(missing)}")

    def _settings(self) -> tuple[str, int, str, str, str]:
        return (
            os.getenv("MYSQL_HOST", "").strip(),
            int(os.getenv("MYSQL_PORT", "3306") or "3306"),
            os.getenv("MYSQL_USER", "").strip(),
            os.getenv("MYSQL_PASSWORD", ""),
            os.getenv("MYSQL_DATABASE", "").strip(),
        )

    def _connect(self):
        self._require_config()
        host, port, user, password, database = self._settings()
        try:
            import pymysql
        except ImportError as exc:
            raise AccountStoreUnavailable("缺少 pymysql 依赖") from exc
        return pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _locked_connection(self):
        store = self

        class _ConnectionContext:
            def __enter__(self):
                store._lock.acquire()
                try:
                    self.conn = store._connect()
                except Exception:
                    store._lock.release()
                    raise
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                try:
                    self.conn.close()
                finally:
                    store._lock.release()

        return _ConnectionContext()

    def _row_to_account(self, row: dict[str, Any]) -> ArticleAccount:
        crypto = self._get_crypto()
        encrypted_phone = str(row["phone"] or "")
        try:
            decrypted_phone = crypto.decrypt_phone(encrypted_phone)
        except Exception as exc:
            raise AccountStoreUnavailable(f"手机号解密失败: {exc}") from exc
        return ArticleAccount(
            id=int(row["id"]),
            phone=decrypted_phone,
            platform=str(row["platform"] or ""),
            channel=str(row["channel"] or ""),
            status=str(row["status"] or "normal"),
            consecutive_failures=int(row["consecutive_failures"] or 0),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            group_id=str(row["group_id"] or ""),
            group_text=str(row["group_text"] or ""),
        )
