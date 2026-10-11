"""Windows PC 版微信 3.x。

数据目录一般是：
  C:\\Users\\<用户>\\Documents\\WeChat Files\\<wxid>\\Msg\\
      MicroMsg.db          联系人（Contact 表）
      Multi\\MSG0.db ...    聊天消息（MSG 表），按大小分片
这些文件都经过 SQLCipher 加密，需要 32 字节密钥（64 位十六进制）。
本工具**不**负责从内存里提取密钥，需要你自己提供本人账号的密钥。

注意：微信 4.x 改用了全新的数据库结构，本模块只支持 3.x。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import sqlcipher
from .model import Chat, Contact, Message
from .protobuf import parse_fields

try:
    import lz4.block as _lz4  # 可选：解压部分 type=49 消息的 CompressContent
except ImportError:  # pragma: no cover
    _lz4 = None


class PCError(Exception):
    pass


def _msg_db_index(path: Path) -> int:
    m = re.fullmatch(r"MSG(\d+)\.db", path.name)
    return int(m.group(1)) if m else -1


def find_databases(msg_dir: str | Path) -> tuple[Path, list[Path]]:
    """返回 (MicroMsg.db, [MSG0.db, MSG1.db, ...])。"""
    msg_dir = Path(msg_dir)
    micro = msg_dir / "MicroMsg.db"
    if not micro.exists():
        raise PCError(f"{msg_dir} 下没有 MicroMsg.db，请指向 'WeChat Files\\<wxid>\\Msg' 目录")
    msg_dbs = sorted((p for p in (msg_dir / "Multi").glob("MSG*.db") if _msg_db_index(p) >= 0),
                     key=_msg_db_index)
    if not msg_dbs:
        raise PCError(f"{msg_dir / 'Multi'} 下没有 MSG*.db")
    return micro, msg_dbs


def decrypt_all(msg_dir: str | Path, key: bytes, out_dir: str | Path,
                log=print) -> Path:
    """把 MicroMsg.db 和所有 MSG*.db 解密到 out_dir，返回 out_dir（结构与原目录一致）。"""
    micro, msg_dbs = find_databases(msg_dir)
    if not sqlcipher.verify_key(micro, key, sqlcipher.PC_WECHAT):
        raise PCError("密钥校验失败：密钥与 MicroMsg.db 不匹配")
    out_dir = Path(out_dir)
    for src in [micro, *msg_dbs]:
        rel = src.relative_to(Path(msg_dir))
        log(f"解密 {rel} ...")
        sqlcipher.decrypt_file(src, out_dir / rel, key, sqlcipher.PC_WECHAT)
    return out_dir


def _sender_from_bytes_extra(blob: bytes | None) -> str:
    """群消息发送者存在 BytesExtra 里：field 3 { field 1 = 1, field 2 = wxid }。"""
    if not blob:
        return ""
    for no, value in parse_fields(blob):
        if no != 3 or not isinstance(value, bytes):
            continue
        sub = dict(parse_fields(value))
        if sub.get(1) == 1 and isinstance(sub.get(2), bytes):
            return sub[2].decode("utf-8", "replace")
    return ""


def _decompress(blob: bytes | None) -> str:
    if not blob or _lz4 is None:
        return ""
    try:
        data = _lz4.decompress(blob, uncompressed_size=len(blob) * 16 + 1024)
        return data.rstrip(b"\x00").decode("utf-8", "replace")
    except Exception:
        return ""


def load_contacts(micro_db: Path) -> dict[str, Contact]:
    con = sqlite3.connect(f"file:{micro_db}?mode=ro", uri=True)
    try:
        return {
            u: Contact(username=u, alias=a or "", remark=r or "", nickname=n or "")
            for u, a, r, n in con.execute("SELECT UserName, Alias, Remark, NickName FROM Contact")
            if u
        }
    finally:
        con.close()


def load(decrypted_dir: str | Path) -> tuple[list[Chat], dict[str, Contact]]:
    """从已解密的目录读取全部聊天。"""
    micro, msg_dbs = find_databases(decrypted_dir)
    contacts = load_contacts(micro)
    chats: dict[str, Chat] = {}
    for db in msg_dbs:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(MSG)")}
            compress = "CompressContent" if "CompressContent" in cols else "NULL"
            rows = con.execute(
                f"SELECT StrTalker, Type, SubType, IsSender, CreateTime, StrContent, "
                f"BytesExtra, {compress} FROM MSG ORDER BY CreateTime"
            )
            for talker, mtype, sub, is_sender, ctime, content, extra, compressed in rows:
                if not talker:
                    continue
                contact = contacts.get(talker) or Contact(username=talker)
                chat = chats.setdefault(talker, Chat(contact))
                content = content or ""
                if mtype == 49 and not content:
                    content = _decompress(compressed)
                sender = ""
                if contact.is_group and not is_sender:
                    sender = _sender_from_bytes_extra(extra)
                chat.messages.append(Message(
                    timestamp=int(ctime or 0), is_sender=bool(is_sender), msg_type=int(mtype or 0),
                    sub_type=int(sub or 0), content=content, sender=sender,
                ))
        finally:
            con.close()
    result = list(chats.values())
    for c in result:
        c.messages.sort(key=lambda m: m.timestamp)
    return result, contacts
