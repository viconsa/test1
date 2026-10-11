"""纯 Python 实现的 SQLCipher 解密（只读），用于微信 PC 版与 Android 版数据库。

PC 版微信 3.x : SQLCipher 3 兼容参数，page 4096，PBKDF2-HMAC-SHA1 64000 次，
               HMAC-SHA1 校验，密码是 32 字节原始密钥。
Android 版微信: SQLCipher 1 兼容参数，page 1024，PBKDF2-HMAC-SHA1 4000 次，
               无 HMAC，密码是 md5(IMEI + uin) 的前 7 位小写十六进制。
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SQLITE_HEADER = b"SQLite format 3\x00"
SALT_SIZE = 16
IV_SIZE = 16
KEY_SIZE = 32


class DecryptError(Exception):
    pass


@dataclass(frozen=True)
class CipherProfile:
    page_size: int
    kdf_iter: int
    use_hmac: bool

    @property
    def reserve(self) -> int:
        size = IV_SIZE + (20 if self.use_hmac else 0)  # HMAC-SHA1 = 20 字节
        return (size + 15) // 16 * 16                 # 按 AES 块大小对齐


PC_WECHAT = CipherProfile(page_size=4096, kdf_iter=64000, use_hmac=True)
ANDROID_WECHAT = CipherProfile(page_size=1024, kdf_iter=4000, use_hmac=False)


def _derive_keys(password: bytes, salt: bytes, profile: CipherProfile) -> tuple[bytes, bytes | None]:
    key = hashlib.pbkdf2_hmac("sha1", password, salt, profile.kdf_iter, KEY_SIZE)
    mac_key = None
    if profile.use_hmac:
        mac_salt = bytes(b ^ 0x3A for b in salt)
        mac_key = hashlib.pbkdf2_hmac("sha1", key, mac_salt, 2, KEY_SIZE)
    return key, mac_key


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


class _PageDecryptor:
    def __init__(self, password: bytes, salt: bytes, profile: CipherProfile):
        self.profile = profile
        self.key, self.mac_key = _derive_keys(password, salt, profile)

    def decrypt(self, page: bytes, page_no: int) -> bytes:
        p = self.profile
        start = SALT_SIZE if page_no == 1 else 0
        data_end = p.page_size - p.reserve
        iv = page[data_end:data_end + IV_SIZE]
        if self.mac_key is not None:
            expected = page[data_end + IV_SIZE:data_end + IV_SIZE + 20]
            mac = hmac.new(self.mac_key, page[start:data_end + IV_SIZE], hashlib.sha1)
            mac.update(struct.pack("<I", page_no))
            if not hmac.compare_digest(mac.digest(), expected):
                raise DecryptError(f"第 {page_no} 页 HMAC 校验失败（密钥错误或文件损坏）")
        plain = _aes_cbc_decrypt(self.key, iv, page[start:data_end])
        if page_no == 1:
            # SQLite 头部第 21~23 字节固定为 64/32/32，可用来验证没有 HMAC 时的密钥
            if plain[5:8] != b"\x40\x20\x20":
                raise DecryptError("解密后的数据库头无效（密钥错误？）")
            return SQLITE_HEADER + plain + page[data_end:]
        return plain + page[data_end:]


def _read_first_page(path: Path, profile: CipherProfile) -> bytes:
    with open(path, "rb") as f:
        page = f.read(profile.page_size)
    if len(page) < profile.page_size:
        raise DecryptError(f"{path} 太小，不是有效的加密数据库")
    if page.startswith(SQLITE_HEADER):
        raise DecryptError(f"{path} 未加密（已经是普通 SQLite 文件）")
    return page


def verify_key(path: str | Path, password: bytes, profile: CipherProfile = PC_WECHAT) -> bool:
    """只解第一页来验证密钥，速度快。"""
    page = _read_first_page(Path(path), profile)
    try:
        _PageDecryptor(password, page[:SALT_SIZE], profile).decrypt(page, 1)
        return True
    except DecryptError:
        return False


def decrypt_file(src: str | Path, dst: str | Path, password: bytes,
                 profile: CipherProfile = PC_WECHAT) -> Path:
    """把加密数据库 src 解密成普通 SQLite 文件 dst。"""
    src, dst = Path(src), Path(dst)
    first = _read_first_page(src, profile)
    dec = _PageDecryptor(password, first[:SALT_SIZE], profile)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        page_no = 0
        while True:
            page = fin.read(profile.page_size)
            if not page:
                break
            page_no += 1
            if len(page) < profile.page_size:
                break  # 末尾不完整的页直接丢弃
            fout.write(dec.decrypt(page, page_no))
    return dst


def parse_hex_key(key: str) -> bytes:
    key = key.strip().lower().removeprefix("0x")
    try:
        raw = bytes.fromhex(key)
    except ValueError as e:
        raise DecryptError("密钥必须是 64 位十六进制字符串") from e
    if len(raw) != KEY_SIZE:
        raise DecryptError(f"密钥长度应为 32 字节（64 个十六进制字符），实际 {len(raw)} 字节")
    return raw


def android_password(imei: str, uin: str | int) -> bytes:
    """Android 版微信数据库密码 = md5(IMEI + uin) 的前 7 位。"""
    return hashlib.md5(f"{imei}{uin}".encode()).hexdigest()[:7].encode()
