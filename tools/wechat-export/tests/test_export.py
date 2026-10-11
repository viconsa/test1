"""端到端测试：为三个平台各构造一份模拟数据，跑完整的命令行流程。

运行：python -m unittest discover -s tests
PC / Android 的加密测试需要 `pip install sqlcipher3-binary`（仅测试用），没有时自动跳过。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_export import sqlcipher  # noqa: E402
from wechat_export.cli import main  # noqa: E402
from wechat_export.model import render_content  # noqa: E402

try:
    import sqlcipher3
except ImportError:  # pragma: no cover
    sqlcipher3 = None

T0 = 1700000000  # 2023-11-14
LINK_XML = "<msg><appmsg><title>好文章</title><type>5</type><url>https://example.com</url></appmsg></msg>"


def run_cli(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main([str(a) for a in argv])
    return code, buf.getvalue()


def pb_str(field_no: int, s: str) -> bytes:
    data = s.encode()
    return bytes([(field_no << 3) | 2, len(data)]) + data


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class IOSTest(TempDirCase):
    def make_backup(self) -> Path:
        backup = self.tmp / "backup"
        backup.mkdir()
        account = hashlib.md5(b"wxid_me").hexdigest()
        files = {}

        def add(rel: str) -> Path:
            fid = hashlib.sha1(rel.encode()).hexdigest()
            files[fid] = rel
            p = backup / fid[:2] / fid
            p.parent.mkdir(exist_ok=True)
            return p

        con = sqlite3.connect(add(f"Documents/{account}/DB/WCDB_Contact.sqlite"))
        con.execute("CREATE TABLE Friend(userName TEXT, dbContactRemark BLOB)")
        con.execute("INSERT INTO Friend VALUES(?,?)",
                    ("wxid_alice", pb_str(1, "Alice") + pb_str(2, "alice123") + pb_str(3, "爱丽丝")))
        con.execute("INSERT INTO Friend VALUES(?,?)", ("123@chatroom", pb_str(1, "家庭群")))
        con.execute("INSERT INTO Friend VALUES(?,?)", ("wxid_bob", pb_str(1, "Bob")))
        con.commit()
        con.close()

        con = sqlite3.connect(add(f"Documents/{account}/DB/message_1.sqlite"))
        for user, rows in {
            "wxid_alice": [(T0, "你好", 1, 1), (T0 + 60, "你好呀", 0, 1), (T0 + 120, LINK_XML, 1, 49),
                           (T0 + 130, "", 1, 3)],
            "123@chatroom": [(T0 + 10, "wxid_bob:\n大家好", 1, 1), (T0 + 20, "收到", 0, 1)],
        }.items():
            t = "Chat_" + hashlib.md5(user.encode()).hexdigest()
            con.execute(f"CREATE TABLE {t}(MesLocalID INTEGER, CreateTime INTEGER, Message TEXT, "
                        f"Des INTEGER, Type INTEGER)")
            con.executemany(f"INSERT INTO {t}(CreateTime, Message, Des, Type) VALUES(?,?,?,?)", rows)
        con.commit()
        con.close()

        con = sqlite3.connect(backup / "Manifest.db")
        con.execute("CREATE TABLE Files(fileID TEXT, domain TEXT, relativePath TEXT, flags INT, file BLOB)")
        con.executemany("INSERT INTO Files VALUES(?, 'AppDomain-com.tencent.xin', ?, 1, NULL)",
                        files.items())
        con.commit()
        con.close()
        return backup

    def test_export_all_formats(self):
        backup = self.make_backup()
        out = self.tmp / "out"
        code, log = run_cli("ios", backup, "-o", out, "-f", "html,txt,csv,json")
        self.assertEqual(code, 0, log)

        txt = (out / "txt" / "爱丽丝.txt").read_text(encoding="utf-8")
        self.assertIn("你好呀", txt)
        self.assertIn("[链接] 好文章 (https://example.com)", txt)
        self.assertIn("[图片]", txt)

        group = json.loads((out / "json" / "家庭群.json").read_text(encoding="utf-8"))
        first = group["messages"][0]
        self.assertEqual((first["sender"], first["sender_name"], first["text"]), ("wxid_bob", "Bob", "大家好"))
        self.assertEqual(group["messages"][1]["sender_name"], "我")

        index = (out / "html" / "index.html").read_text(encoding="utf-8")
        self.assertIn("爱丽丝.html", index)
        self.assertTrue((out / "csv" / "家庭群.csv").exists())

    def test_filter_and_list(self):
        backup = self.make_backup()
        code, log = run_cli("ios", backup, "--list", "-c", "alice")
        self.assertEqual(code, 0, log)
        self.assertIn("爱丽丝", log)
        self.assertNotIn("家庭群", log)

        code, log = run_cli("ios", backup, "--list", "--since", "2030-01-01")
        self.assertIn("共 0 个会话", log)

    def test_not_a_backup(self):
        code, log = run_cli("ios", self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("Manifest.db", log)


def make_encrypted(path: Path, password: str, pragmas: list[str], schema: list[str],
                   inserts: list[tuple[str, list[tuple]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlcipher3.connect(str(path))
    con.execute(f"PRAGMA key='{password}'")
    for p in pragmas:
        con.execute(p)
    for s in schema:
        con.execute(s)
    for sql, rows in inserts:
        con.executemany(sql, rows)
    con.commit()
    con.close()


PC_PRAGMAS = ["PRAGMA cipher_page_size=4096", "PRAGMA kdf_iter=64000",
              "PRAGMA cipher_hmac_algorithm=HMAC_SHA1", "PRAGMA cipher_kdf_algorithm=PBKDF2_HMAC_SHA1"]
# PC 版密钥是 32 字节原始数据；测试里选一个全 ASCII 的，方便用 sqlcipher3 的字符串 PRAGMA key 建库
PC_KEY_ASCII = "WeChatTestKey_0123456789abcdefgh"


@unittest.skipIf(sqlcipher3 is None, "需要 sqlcipher3-binary")
class PCTest(TempDirCase):
    def make_msg_dir(self) -> Path:
        msg = self.tmp / "WeChat Files" / "wxid_me" / "Msg"
        make_encrypted(msg / "MicroMsg.db", PC_KEY_ASCII, PC_PRAGMAS,
                       ["CREATE TABLE Contact(UserName TEXT, Alias TEXT, Remark TEXT, NickName TEXT)"],
                       [("INSERT INTO Contact VALUES(?,?,?,?)",
                         [("wxid_alice", "alice123", "", "Alice"), ("456@chatroom", "", "", "工作群"),
                          ("wxid_bob", "", "老板", "Bob")])])
        msg_schema = ["CREATE TABLE MSG(localId INTEGER PRIMARY KEY, Type INT, SubType INT, IsSender INT, "
                      "CreateTime INT, StrTalker TEXT, StrContent TEXT, BytesExtra BLOB, CompressContent BLOB)"]
        sql = ("INSERT INTO MSG(Type, SubType, IsSender, CreateTime, StrTalker, StrContent, BytesExtra) "
               "VALUES(?,?,?,?,?,?,?)")
        sender_blob = bytes([0x1A]) + bytes([len(b := bytes([0x08, 0x01]) + pb_str(2, "wxid_bob"))]) + b
        make_encrypted(msg / "Multi" / "MSG0.db", PC_KEY_ASCII, PC_PRAGMAS, msg_schema,
                       [(sql, [(1, 0, 0, T0, "wxid_alice", "在吗", None),
                               (1, 0, 1, T0 + 5, "wxid_alice", "在", None)])])
        make_encrypted(msg / "Multi" / "MSG1.db", PC_KEY_ASCII, PC_PRAGMAS, msg_schema,
                       [(sql, [(1, 0, 0, T0 + 100, "456@chatroom", "明天开会", sender_blob),
                               (10000, 0, 0, T0 + 200, "456@chatroom", "\"Bob\"撤回了一条消息", None)])])
        return msg

    def test_export(self):
        msg = self.make_msg_dir()
        out = self.tmp / "out"
        key_hex = PC_KEY_ASCII.encode().hex()
        code, log = run_cli("pc", msg, "-k", key_hex, "-o", out, "-f", "txt,json")
        self.assertEqual(code, 0, log)
        self.assertIn("在吗", (out / "txt" / "Alice.txt").read_text(encoding="utf-8"))
        group = json.loads((out / "json" / "工作群.json").read_text(encoding="utf-8"))
        self.assertEqual(group["messages"][0]["sender_name"], "老板")
        self.assertEqual(group["messages"][1]["text"], "[系统] \"Bob\"撤回了一条消息")

        # 用已解密目录再跑一次
        code, log = run_cli("pc", "--decrypted", out / "_decrypted", "--list")
        self.assertEqual(code, 0, log)
        self.assertIn("工作群", log)

    def test_wrong_key(self):
        msg = self.make_msg_dir()
        code, log = run_cli("pc", msg, "-k", "00" * 32, "-o", self.tmp / "out")
        self.assertEqual(code, 1)
        self.assertIn("密钥校验失败", log)

    def test_decrypt_command(self):
        msg = self.make_msg_dir()
        key_hex = PC_KEY_ASCII.encode().hex()
        code, log = run_cli("decrypt", "pc", msg / "MicroMsg.db", "-k", key_hex, "--verify-only")
        self.assertEqual((code, log.strip()), (0, "密钥正确。"))
        out = self.tmp / "plain.db"
        code, log = run_cli("decrypt", "pc", msg / "MicroMsg.db", out, "-k", key_hex)
        self.assertEqual(code, 0, log)
        self.assertEqual(sqlite3.connect(out).execute("SELECT count(*) FROM Contact").fetchone()[0], 3)


@unittest.skipIf(sqlcipher3 is None, "需要 sqlcipher3-binary")
class AndroidTest(TempDirCase):
    def test_export(self):
        password = sqlcipher.android_password("1234567890ABCDEF", "-123456").decode()
        db = self.tmp / "EnMicroMsg.db"
        make_encrypted(
            db, password,
            ["PRAGMA cipher_compatibility=1", "PRAGMA cipher_page_size=1024",
             "PRAGMA kdf_iter=4000", "PRAGMA cipher_use_hmac=OFF"],
            ["CREATE TABLE rcontact(username TEXT, alias TEXT, conRemark TEXT, nickname TEXT)",
             "CREATE TABLE message(msgId INTEGER PRIMARY KEY, type INT, isSend INT, createTime INT, "
             "talker TEXT, content TEXT)"],
            [("INSERT INTO rcontact VALUES(?,?,?,?)", [("wxid_carol", "", "小C", "Carol")]),
             ("INSERT INTO message(type, isSend, createTime, talker, content) VALUES(?,?,?,?,?)",
              [(1, 0, T0 * 1000, "wxid_carol", "早上好"),
               (436207665, 1, T0 * 1000 + 5000, "wxid_carol", LINK_XML)])],
        )
        out = self.tmp / "out"
        code, log = run_cli("android", db, "--uin", "-123456", "-o", out, "-f", "txt")
        self.assertEqual(code, 0, log)
        txt = (out / "txt" / "小C.txt").read_text(encoding="utf-8")
        self.assertIn("早上好", txt)
        self.assertIn("[链接] 好文章", txt)

        code, log = run_cli("android", db, "--uin", "999", "-o", out)
        self.assertEqual(code, 1)
        self.assertIn("密码校验失败", log)


class RenderTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(render_content(34, ""), "[语音]")
        self.assertEqual(render_content(48, '<msg><location poiname="天安门" /></msg>'), "[位置] 天安门")
        self.assertEqual(
            render_content(49, "<msg><appmsg><title>回复内容</title><type>57</type>"
                               "<refermsg><displayname>Bob</displayname><content>原话</content></refermsg>"
                               "</appmsg></msg>"),
            "回复内容 「Bob: 原话」")
        self.assertEqual(render_content(49, "<msg><appmsg><title>报告.pdf</title><type>6</type></appmsg></msg>"),
                         "[文件] 报告.pdf")
        self.assertEqual(render_content(49, "not xml"), "[分享]")
        self.assertEqual(render_content(12345, "x"), "[未知消息类型 12345]")


if __name__ == "__main__":
    unittest.main()
