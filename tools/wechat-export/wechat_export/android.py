"""Android 版微信。

数据库位置（需要 root，或通过手机厂商的备份功能导出）：
  /data/data/com.tencent.mm/MicroMsg/<md5>/EnMicroMsg.db
密码 = md5(IMEI + uin) 的前 7 位小写十六进制：
  - uin 在 /data/data/com.tencent.mm/shared_prefs/auth_info_key_prefs.xml 的 _auth_uin
  - 新版 Android 拿不到 IMEI 时，微信使用 "1234567890ABCDEF" 代替
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import sqlcipher
from .model import Chat, Contact, Message, split_group_sender

DEFAULT_IMEI = "1234567890ABCDEF"


class AndroidError(Exception):
    pass


def decrypt(db: str | Path, password: bytes, out: str | Path) -> Path:
    if not sqlcipher.verify_key(db, password, sqlcipher.ANDROID_WECHAT):
        raise AndroidError("密码校验失败：请检查 IMEI / uin（或直接用 --password 指定 7 位密码）")
    return sqlcipher.decrypt_file(db, out, password, sqlcipher.ANDROID_WECHAT)


def load(decrypted_db: str | Path) -> tuple[list[Chat], dict[str, Contact]]:
    con = sqlite3.connect(f"file:{decrypted_db}?mode=ro", uri=True)
    try:
        contacts = {
            u: Contact(username=u, alias=a or "", remark=r or "", nickname=n or "")
            for u, a, r, n in con.execute("SELECT username, alias, conRemark, nickname FROM rcontact")
            if u
        }
        chats: dict[str, Chat] = {}
        for talker, mtype, is_send, ctime, content in con.execute(
            "SELECT talker, type, isSend, createTime, content FROM message ORDER BY createTime"
        ):
            if not talker:
                continue
            contact = contacts.get(talker) or Contact(username=talker)
            chat = chats.setdefault(talker, Chat(contact))
            content = content or ""
            sender = ""
            if contact.is_group and not is_send:
                sender, content = split_group_sender(content)
            chat.messages.append(Message(
                timestamp=int(ctime or 0) // 1000,  # Android 存的是毫秒
                is_sender=bool(is_send), msg_type=int(mtype or 0) & 0xFFFF,
                content=content, sender=sender,
            ))
    finally:
        con.close()
    return list(chats.values()), contacts
