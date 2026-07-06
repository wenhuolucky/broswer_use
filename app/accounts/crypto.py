"""手机号加密/解密。

使用 AES-256-SIV 确定性加密：相同明文 + 相同密钥 = 相同密文，
可直接用密文做 WHERE phone = ? 查询，无需额外索引字段。
密文格式：enc:v1:<base64url(ciphertext)>。
"""

from __future__ import annotations

import base64
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from app.accounts.errors import AccountStoreUnavailable

# 密文版本前缀
_CIPHER_PREFIX = "enc:v1:"

# 国内手机号正则：11 位，1 开头
_PHONE_RE = re.compile(r"^1\d{10}$")


def normalize_phone(phone: str) -> str:
    """归一化手机号，用于加密前统一格式。

    规则：去除空格和连字符，不带国家码，必须 11 位以 1 开头。
    """
    normalized = phone.replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(normalized):
        raise ValueError("手机号格式不正确")
    return normalized


class PhoneCrypto:
    """手机号加密/解密。

    从环境变量 ACCOUNT_PHONE_ENCRYPTION_KEY 读取 32 bytes 主密钥，
    通过 HKDF-SHA256 派生 64 bytes 密钥给 AES-256-SIV。
    """

    def __init__(self) -> None:
        master_key = self._load_master_key()
        self._siv_key = self._derive_key(master_key)

    @staticmethod
    def _load_master_key() -> bytes:
        """从环境变量加载主密钥，缺失或格式非法时抛异常。"""
        raw = os.getenv("ACCOUNT_PHONE_ENCRYPTION_KEY", "").strip()
        if not raw:
            raise AccountStoreUnavailable(
                "缺少 ACCOUNT_PHONE_ENCRYPTION_KEY 环境变量"
            )
        # 补齐 base64url padding（生成命令通常省略尾部 =）
        padded = raw + "=" * (4 - len(raw) % 4) if len(raw) % 4 else raw
        try:
            key = base64.urlsafe_b64decode(padded)
        except Exception as exc:
            raise AccountStoreUnavailable(
                "ACCOUNT_PHONE_ENCRYPTION_KEY 格式不合法（需 base64url 编码）"
            ) from exc
        if len(key) != 32:
            raise AccountStoreUnavailable(
                f"ACCOUNT_PHONE_ENCRYPTION_KEY 长度须为 32 bytes，实际 {len(key)} bytes"
            )
        return key

    @staticmethod
    def _derive_key(master_key: bytes) -> bytes:
        """从主密钥通过 HKDF-SHA256 派生 64 bytes 密钥给 AES-256-SIV。"""
        return HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=None,
            info=b"account-phone-encryption",
        ).derive(master_key)

    def encrypt_phone(self, phone: str) -> str:
        """确定性加密手机号，返回 enc:v1:<base64url(ciphertext)>。

        同一手机号每次加密结果相同，可直接用于数据库查询。
        """
        normalized = normalize_phone(phone)
        aessiv = AESSIV(self._siv_key)
        ciphertext = aessiv.encrypt(normalized.encode("utf-8"), None)
        encoded = base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("=")
        return f"{_CIPHER_PREFIX}{encoded}"

    def decrypt_phone(self, encrypted: str) -> str:
        """解密密文手机号，返回明文。非密文格式时原样返回（兼容历史明文）。"""
        if not encrypted.startswith(_CIPHER_PREFIX):
            return encrypted
        encoded = encrypted[len(_CIPHER_PREFIX):]
        # base64url 解码前补齐 padding
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        ciphertext = base64.urlsafe_b64decode(encoded)
        aessiv = AESSIV(self._siv_key)
        try:
            plaintext = aessiv.decrypt(ciphertext, None)
        except Exception as exc:
            raise ValueError("手机号解密失败") from exc
        return plaintext.decode("utf-8")


def is_encrypted(phone: str) -> bool:
    """判断 phone 字段值是否为加密格式。"""
    return phone.startswith(_CIPHER_PREFIX)
