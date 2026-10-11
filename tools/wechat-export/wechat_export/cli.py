"""命令行入口：python -m wechat_export <子命令> ..."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import android, exporters, pc, sqlcipher
from .ios import IOSBackup, IOSBackupError


def _date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"无法识别的日期：{s}（格式 YYYY-MM-DD）")


def _formats(s: str) -> list[str]:
    fmts = [f.strip().lower() for f in s.split(",") if f.strip()]
    bad = [f for f in fmts if f not in exporters.FORMATS]
    if bad or not fmts:
        raise argparse.ArgumentTypeError(f"不支持的格式 {bad}，可选：{','.join(exporters.FORMATS)}")
    return fmts


def _add_export_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--out", default="wechat_export_output", help="输出目录（默认 ./wechat_export_output）")
    p.add_argument("-f", "--format", type=_formats, default=["html", "txt"],
                   help="导出格式，逗号分隔：html,txt,csv,json（默认 html,txt）")
    p.add_argument("-c", "--chat", action="append",
                   help="只导出名称/备注/wxid 包含该关键字的会话，可重复指定")
    p.add_argument("--since", type=_date, help="起始日期（含），如 2024-01-01")
    p.add_argument("--until", type=_date, help="截止日期（不含），如 2025-01-01")
    p.add_argument("--me", default="我", help="导出中“我”的显示名（默认：我）")
    p.add_argument("--list", action="store_true", help="只列出会话，不导出")


def _finish(chats, contacts, args) -> int:
    chats = exporters.filter_chats(chats, args.chat, args.since, args.until)
    if args.list:
        for c in chats:
            last = c.messages[-1].time.strftime("%Y-%m-%d") if c.messages else ""
            print(f"{len(c.messages):>7} 条  {last}  {c.contact.display_name}  ({c.contact.username})")
        print(f"共 {len(chats)} 个会话")
        return 0
    if not chats:
        print("没有符合条件的会话。", file=sys.stderr)
        return 1
    exporters.export(chats, contacts, args.out, args.format, me=args.me)
    return 0


def cmd_ios(args) -> int:
    backup = IOSBackup(args.backup)
    if args.account is None and len(backup.accounts) > 1:
        print("备份中有多个微信账号，请用 --account 指定：")
        for acc, files in backup.accounts.items():
            print(f"  {acc}  ({len(files)} 个数据库文件)")
        return 1
    chats, contacts = backup.load(args.account)
    return _finish(chats, contacts, args)


def cmd_pc(args) -> int:
    if args.decrypted:
        work = Path(args.decrypted)
    else:
        if not args.key:
            print("需要 --key（64 位十六进制密钥），或用 --decrypted 指定已解密目录", file=sys.stderr)
            return 2
        key = sqlcipher.parse_hex_key(args.key)
        work = Path(args.out) / "_decrypted"
        pc.decrypt_all(args.msg_dir, key, work)
    chats, contacts = pc.load(work)
    return _finish(chats, contacts, args)


def cmd_android(args) -> int:
    if args.decrypted:
        db = Path(args.decrypted)
    else:
        if args.password:
            password = args.password.encode()
        elif args.uin:
            password = sqlcipher.android_password(args.imei, args.uin)
        else:
            print("需要 --uin（可配合 --imei），或 --password", file=sys.stderr)
            return 2
        db = Path(args.out) / "_decrypted" / "EnMicroMsg.db"
        print(f"解密 {args.db} ...")
        android.decrypt(args.db, password, db)
    chats, contacts = android.load(db)
    return _finish(chats, contacts, args)


def cmd_decrypt(args) -> int:
    if args.platform == "pc":
        password, profile = sqlcipher.parse_hex_key(args.key), sqlcipher.PC_WECHAT
    else:
        password, profile = args.key.encode(), sqlcipher.ANDROID_WECHAT
    if not sqlcipher.verify_key(args.input, password, profile):
        print("密钥错误。", file=sys.stderr)
        return 1
    if args.verify_only:
        print("密钥正确。")
        return 0
    sqlcipher.decrypt_file(args.input, args.output, password, profile)
    print(f"已解密 → {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wechat_export",
        description="导出你自己的微信聊天记录为 HTML / TXT / CSV / JSON。",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ios", help="从 iPhone 的 iTunes/Finder 未加密备份导出（无需密钥，推荐）")
    p.add_argument("backup", help="备份目录（含 Manifest.db）")
    p.add_argument("--account", help="备份中有多个微信账号时指定（32 位哈希）")
    _add_export_args(p)
    p.set_defaults(func=cmd_ios)

    p = sub.add_parser("pc", help="从 Windows 微信 3.x 的数据目录导出（需要密钥）")
    p.add_argument("msg_dir", nargs="?", help=r"'WeChat Files\<wxid>\Msg' 目录")
    p.add_argument("-k", "--key", help="32 字节数据库密钥（64 位十六进制）")
    p.add_argument("--decrypted", help="已解密的目录（结构同 Msg 目录），跳过解密步骤")
    _add_export_args(p)
    p.set_defaults(func=cmd_pc)

    p = sub.add_parser("android", help="从 Android 的 EnMicroMsg.db 导出")
    p.add_argument("db", nargs="?", help="EnMicroMsg.db 路径")
    p.add_argument("--uin", help="微信 uin（auth_info_key_prefs.xml 中的 _auth_uin）")
    p.add_argument("--imei", default=android.DEFAULT_IMEI,
                   help=f"手机 IMEI（默认 {android.DEFAULT_IMEI}，新版 Android 微信使用该值）")
    p.add_argument("--password", help="直接指定 7 位数据库密码")
    p.add_argument("--decrypted", help="已解密的 EnMicroMsg.db，跳过解密步骤")
    _add_export_args(p)
    p.set_defaults(func=cmd_android)

    p = sub.add_parser("decrypt", help="只解密单个数据库文件 / 验证密钥")
    p.add_argument("platform", choices=["pc", "android"])
    p.add_argument("input")
    p.add_argument("output", nargs="?", default="decrypted.db")
    p.add_argument("-k", "--key", required=True, help="pc：64 位十六进制密钥；android：7 位密码")
    p.add_argument("--verify-only", action="store_true", help="只检查密钥是否正确")
    p.set_defaults(func=cmd_decrypt)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "pc" and not args.msg_dir and not args.decrypted:
        print("请指定 Msg 目录或 --decrypted", file=sys.stderr)
        return 2
    if args.cmd == "android" and not args.db and not args.decrypted:
        print("请指定 EnMicroMsg.db 路径或 --decrypted", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (IOSBackupError, pc.PCError, android.AndroidError, sqlcipher.DecryptError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
