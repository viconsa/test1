"""把统一格式的聊天记录导出为 TXT / CSV / JSON / HTML。"""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path

from .model import Chat, Contact, Message

FORMATS = ("html", "txt", "csv", "json")


def safe_filename(name: str, used: set[str]) -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "未命名"
    base = base[:80]
    candidate, n = base, 2
    while candidate.lower() in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate.lower())
    return candidate


def filter_chats(chats: list[Chat], names: list[str] | None = None,
                 since: datetime | None = None, until: datetime | None = None,
                 include_empty: bool = False) -> list[Chat]:
    lo = int(since.timestamp()) if since else None
    hi = int(until.timestamp()) if until else None
    out = []
    for chat in chats:
        c = chat.contact
        if names and not any(
            n.lower() in field.lower()
            for n in names for field in (c.display_name, c.username, c.nickname, c.alias) if field
        ):
            continue
        msgs = [m for m in chat.messages
                if (lo is None or m.timestamp >= lo) and (hi is None or m.timestamp < hi)]
        if msgs or include_empty:
            out.append(Chat(c, msgs))
    out.sort(key=lambda ch: ch.messages[-1].timestamp if ch.messages else 0, reverse=True)
    return out


class _Namer:
    def __init__(self, contacts: dict[str, Contact], me: str):
        self.contacts = contacts
        self.me = me

    def __call__(self, chat: Chat, msg: Message) -> str:
        if msg.is_sender:
            return self.me
        if chat.contact.is_group:
            if not msg.sender:
                return "系统" if msg.msg_type in (10000, 10002) else "?"
            c = self.contacts.get(msg.sender)
            return c.display_name if c else msg.sender
        return chat.contact.display_name


def _fmt_time(m: Message) -> str:
    return m.time.strftime("%Y-%m-%d %H:%M:%S")


def write_txt(chat: Chat, path: Path, name_of) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"与「{chat.contact.display_name}」的聊天记录（{chat.contact.username}）\n")
        f.write(f"共 {len(chat.messages)} 条\n{'=' * 60}\n")
        for m in chat.messages:
            f.write(f"{_fmt_time(m)}  {name_of(chat, m)}\n{m.text}\n\n")


def write_csv(chat: Chat, path: Path, name_of) -> None:
    # utf-8-sig 让 Excel 直接打开不乱码
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["时间", "发送者", "是否我发送", "类型", "内容"])
        for m in chat.messages:
            w.writerow([_fmt_time(m), name_of(chat, m), int(m.is_sender), m.msg_type, m.text])


def write_json(chat: Chat, path: Path, name_of) -> None:
    data = {
        "contact": {
            "username": chat.contact.username, "nickname": chat.contact.nickname,
            "remark": chat.contact.remark, "alias": chat.contact.alias,
            "is_group": chat.contact.is_group,
        },
        "messages": [{
            "time": _fmt_time(m), "timestamp": m.timestamp, "is_sender": m.is_sender,
            "sender": m.sender, "sender_name": name_of(chat, m),
            "type": m.msg_type, "text": m.text, "raw": m.content,
        } for m in chat.messages],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


_HTML_HEAD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#ededed; --card:#fff; --me:#95ec69; --fg:#111; --muted:#888; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#111; --card:#2c2c2c; --me:#3eb575; --fg:#eee; --muted:#888; }}
}}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
header {{ position:sticky; top:0; background:var(--card); padding:12px 16px;
  border-bottom:1px solid rgba(128,128,128,.25); font-weight:600; }}
main {{ max-width:820px; margin:0 auto; padding:12px 16px 48px; }}
.day {{ text-align:center; color:var(--muted); font-size:12px; margin:18px 0 6px; }}
.row {{ display:flex; flex-direction:column; align-items:flex-start; margin:6px 0; }}
.row.me {{ align-items:flex-end; }}
.meta {{ color:var(--muted); font-size:12px; margin:0 4px 2px; }}
.bubble {{ background:var(--card); padding:8px 12px; border-radius:6px; max-width:75%;
  white-space:pre-wrap; word-break:break-word; }}
.me .bubble {{ background:var(--me); color:#111; }}
.sys {{ text-align:center; color:var(--muted); font-size:12px; margin:8px 0; }}
a {{ color:inherit; }}
ul {{ list-style:none; padding:0; }}
li a {{ display:flex; justify-content:space-between; background:var(--card); padding:10px 14px;
  margin:4px 0; border-radius:6px; text-decoration:none; }}
li small {{ color:var(--muted); }}
</style></head><body>
"""


def write_html(chat: Chat, path: Path, name_of) -> None:
    e = html.escape
    parts = [_HTML_HEAD.format(title=e(chat.contact.display_name)),
             f'<header><a href="index.html">←</a>&nbsp; {e(chat.contact.display_name)}'
             f' <small>({len(chat.messages)} 条)</small></header><main>']
    last_day = None
    for m in chat.messages:
        day = m.time.strftime("%Y-%m-%d")
        if day != last_day:
            parts.append(f'<div class="day">{day}</div>')
            last_day = day
        if m.msg_type in (10000, 10002):
            parts.append(f'<div class="sys">{e(m.text)}</div>')
            continue
        cls = "row me" if m.is_sender else "row"
        parts.append(
            f'<div class="{cls}"><div class="meta">{e(name_of(chat, m))} · {m.time:%H:%M:%S}</div>'
            f'<div class="bubble">{e(m.text)}</div></div>'
        )
    parts.append("</main></body></html>\n")
    path.write_text("".join(parts), encoding="utf-8")


def write_index(entries: list[tuple[Chat, str]], path: Path) -> None:
    e = html.escape
    parts = [_HTML_HEAD.format(title="微信聊天记录"),
             f"<header>微信聊天记录（{len(entries)} 个会话）</header><main><ul>"]
    for chat, filename in entries:
        last = chat.messages[-1].time.strftime("%Y-%m-%d") if chat.messages else ""
        parts.append(
            f'<li><a href="{e(filename)}"><span>{e(chat.contact.display_name)}</span>'
            f"<small>{len(chat.messages)} 条 · {last}</small></a></li>"
        )
    parts.append("</ul></main></body></html>\n")
    path.write_text("".join(parts), encoding="utf-8")


WRITERS = {"txt": write_txt, "csv": write_csv, "json": write_json, "html": write_html}


def export(chats: list[Chat], contacts: dict[str, Contact], out_dir: str | Path,
           formats: list[str], me: str = "我", log=print) -> Path:
    out_dir = Path(out_dir)
    name_of = _Namer(contacts, me)
    used: set[str] = {"index"}
    index_entries = []
    for fmt in formats:
        (out_dir / fmt).mkdir(parents=True, exist_ok=True)
    for chat in chats:
        stem = safe_filename(chat.contact.display_name, used)
        for fmt in formats:
            WRITERS[fmt](chat, out_dir / fmt / f"{stem}.{fmt}", name_of)
        index_entries.append((chat, f"{stem}.html"))
    if "html" in formats:
        write_index(index_entries, out_dir / "html" / "index.html")
    total = sum(len(c.messages) for c in chats)
    log(f"完成：{len(chats)} 个会话，{total} 条消息 → {out_dir.resolve()}")
    return out_dir
