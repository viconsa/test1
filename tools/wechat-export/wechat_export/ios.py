"""iOS：从 iTunes / Finder 的**未加密**本地备份中读取微信聊天记录。

iOS 版微信的数据库在备份里本身是明文 SQLite，不需要任何密钥：
  AppDomain-com.tencent.xin / Documents/<md5(我的wxid)>/DB/MM.sqlite
                                                       /DB/message_{1..4}.sqlite
                                                       /DB/WCDB_Contact.sqlite
消息表名为 Chat_<md5(对方username)>，Des=0 表示我发出的。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from .model import Chat, Contact, Message, split_group_sender
from .protobuf import first_strings

WECHAT_DOMAIN = "AppDomain-com.tencent.xin"
_ACCOUNT_RE = re.compile(r"^Documents/([0-9a-f]{32})/DB/([^/]+\.sqlite)$")


class IOSBackupError(Exception):
    pass


class IOSBackup:
    def __init__(self, backup_dir: str | Path):
        self.root = Path(backup_dir)
        manifest = self.root / "Manifest.db"
        if not manifest.exists():
            raise IOSBackupError(f"{self.root} 中没有 Manifest.db，不是 iOS 备份目录")
        try:
            con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT fileID, relativePath FROM Files WHERE domain = ?", (WECHAT_DOMAIN,)
            ).fetchall()
            con.close()
        except sqlite3.DatabaseError as e:
            raise IOSBackupError(
                "无法读取 Manifest.db —— 备份可能是加密的，请在 iTunes/Finder 中取消“加密本地备份”后重新备份"
            ) from e
        # account_hash -> {文件名: 备份内实际路径}
        self.accounts: dict[str, dict[str, Path]] = {}
        for file_id, rel in rows:
            m = _ACCOUNT_RE.match(rel or "")
            if m:
                path = self.root / file_id[:2] / file_id
                if path.exists():
                    self.accounts.setdefault(m.group(1), {})[m.group(2)] = path
        if not self.accounts:
            raise IOSBackupError("备份中没有找到微信数据（确认备份时手机上安装并登录了微信）")

    def load(self, account: str | None = None) -> tuple[list[Chat], dict[str, Contact]]:
        if account is None:
            if len(self.accounts) > 1:
                raise IOSBackupError(
                    "备份中有多个微信账号，请用 --account 指定其中一个：" + ", ".join(self.accounts)
                )
            account = next(iter(self.accounts))
        files = self.accounts.get(account)
        if files is None:
            raise IOSBackupError(f"找不到账号 {account}")

        contacts = self._load_contacts(files)
        by_hash = {hashlib.md5(u.encode()).hexdigest(): c for u, c in contacts.items()}

        chats: dict[str, Chat] = {}
        msg_dbs = [n for n in sorted(files) if n == "MM.sqlite" or n.startswith("message_")]
        for name in msg_dbs:
            con = sqlite3.connect(f"file:{files[name]}?mode=ro", uri=True)
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Chat\\_%' ESCAPE '\\'"
            )]
            for table in tables:
                h = table[5:]
                if not re.fullmatch(r"[0-9a-f]{32}", h):
                    continue
                contact = by_hash.get(h) or Contact(username=h)
                chat = chats.setdefault(h, Chat(contact))
                for create_time, content, des, mtype in con.execute(
                    f'SELECT CreateTime, Message, Des, Type FROM "{table}" ORDER BY CreateTime'
                ):
                    content = content or ""
                    is_sender = des == 0
                    sender = ""
                    if contact.is_group and not is_sender:
                        sender, content = split_group_sender(content)
                    chat.messages.append(Message(
                        timestamp=int(create_time or 0), is_sender=is_sender,
                        msg_type=int(mtype or 0), content=content, sender=sender,
                    ))
            con.close()
        result = [c for c in chats.values() if c.messages]
        for c in result:
            c.messages.sort(key=lambda m: m.timestamp)
        return result, contacts

    @staticmethod
    def _load_contacts(files: dict[str, Path]) -> dict[str, Contact]:
        out: dict[str, Contact] = {}
        for name in ("WCDB_Contact.sqlite", "MM.sqlite"):
            if name not in files:
                continue
            con = sqlite3.connect(f"file:{files[name]}?mode=ro", uri=True)
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(Friend)")}
                if "userName" not in cols:
                    continue
                remark_col = "dbContactRemark" if "dbContactRemark" in cols else "NULL"
                for username, remark_blob in con.execute(
                    f"SELECT userName, {remark_col} FROM Friend"
                ):
                    if not username or username in out:
                        continue
                    fields = first_strings(remark_blob) if isinstance(remark_blob, bytes) else {}
                    out[username] = Contact(
                        username=username,
                        nickname=fields.get(1, ""),
                        alias=fields.get(2, ""),
                        remark=fields.get(3, ""),
                    )
            finally:
                con.close()
        return out
