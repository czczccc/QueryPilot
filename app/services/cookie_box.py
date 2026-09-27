"""夸克登录凭证加密：AES-256-GCM，密钥只在服务器上，数据库里只有密文。

密钥来源（优先级）：
1. 环境变量 `COOKIE_SECRET`（任意长字符串，经 SHA-256 派生为 32 字节密钥）；
2. 否则自动生成随机密钥写入 `data/.cookie_secret`（与数据库同目录但分文件，权限 600）。
换掉密钥后，旧凭证无法解密，用户重新扫码即可。
"""

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CookieBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("密钥必须是 32 字节")
        self._aes = AESGCM(key)

    def __repr__(self) -> str:
        return "CookieBox()"

    @classmethod
    def from_secret(cls, secret: str) -> "CookieBox":
        return cls(hashlib.sha256(secret.encode()).digest())

    @classmethod
    def load(cls, secret: str, key_file: str | Path) -> "CookieBox":
        """有 `secret` 用它；否则读取或生成密钥文件。"""
        if secret:
            return cls.from_secret(secret)
        path = Path(key_file)
        if path.exists():
            return cls(base64.b64decode(path.read_text().strip()))
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        # 先建好 600 权限的文件再写入，避免密钥短暂可被其他用户读取
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(key).decode())
        return cls(key)

    def encrypt(self, plaintext: str, aad: bytes = b"") -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, plaintext.encode(), aad)

    def decrypt(self, blob: bytes, aad: bytes = b"") -> str | None:
        """解密失败（密钥换了、数据被改）返回 None。"""
        try:
            return self._aes.decrypt(blob[:12], blob[12:], aad).decode()
        except (InvalidTag, ValueError):
            return None


def session_hash(session: str) -> str:
    """浏览器会话令牌只存哈希：数据库泄露也拿不到可用的会话。"""
    return hashlib.sha256(session.encode()).hexdigest()
