"""通用数据模型：不同来源（iOS / PC / Android）解析后统一成这些结构。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Contact:
    username: str          # wxid_xxx / xxx@chatroom
    nickname: str = ""
    remark: str = ""
    alias: str = ""        # 微信号

    @property
    def display_name(self) -> str:
        return self.remark or self.nickname or self.alias or self.username

    @property
    def is_group(self) -> bool:
        return self.username.endswith("@chatroom")


@dataclass
class Message:
    timestamp: int         # Unix 秒
    is_sender: bool        # True = 我发出的
    msg_type: int
    content: str           # 原始内容
    sender: str = ""       # 发送者 username（群聊里有意义）
    sub_type: int = 0

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    @property
    def text(self) -> str:
        return render_content(self.msg_type, self.content, self.sub_type)


@dataclass
class Chat:
    contact: Contact
    messages: list[Message] = field(default_factory=list)


# ---------------------------------------------------------------- 内容渲染

TYPE_LABELS = {
    3: "[图片]",
    34: "[语音]",
    42: "[名片]",
    43: "[视频]",
    47: "[表情]",
    48: "[位置]",
    50: "[语音/视频通话]",
    62: "[小视频]",
}

APP_TYPE_LABELS = {
    3: "音乐",
    4: "视频",
    5: "链接",
    6: "文件",
    8: "表情",
    19: "聊天记录",
    33: "小程序",
    36: "小程序",
    51: "视频号",
    57: "引用",
    2000: "转账",
    2001: "红包",
}


def _xml_root(text: str):
    """尽量把消息内容当成 XML 解析，失败返回 None。"""
    if not text or "<" not in text:
        return None
    start = text.find("<")
    try:
        return ET.fromstring(text[start:].strip())
    except ET.ParseError:
        return None


def _find_text(root, path: str) -> str:
    node = root.find(path)
    return (node.text or "").strip() if node is not None and node.text else ""


def render_content(msg_type: int, content: str, sub_type: int = 0) -> str:
    """把各种类型的消息转成人类可读的一行文字。"""
    content = content or ""
    if msg_type == 1:
        return content
    if msg_type == 10000:  # 系统消息（撤回、入群提示等）
        return "[系统] " + re.sub(r"<[^>]+>", "", content).strip()
    if msg_type == 10002:  # 撤回等 sysmsg XML
        root = _xml_root(content)
        if root is not None:
            txt = _find_text(root, ".//replacemsg") or _find_text(root, ".//content")
            if txt:
                return "[系统] " + txt
        return "[系统消息]"
    if msg_type == 48:
        root = _xml_root(content)
        if root is not None:
            loc = root.find(".//location")
            if loc is not None:
                label = loc.get("poiname") or loc.get("label") or ""
                return f"[位置] {label}".rstrip()
        return "[位置]"
    if msg_type == 42:
        root = _xml_root(content)
        if root is not None and root.get("nickname"):
            return f"[名片] {root.get('nickname')}"
        return "[名片]"
    if msg_type == 49:
        root = _xml_root(content)
        if root is None:
            return "[分享]"
        app_type = _find_text(root, ".//appmsg/type")
        title = _find_text(root, ".//appmsg/title")
        try:
            label = APP_TYPE_LABELS.get(int(app_type), "分享")
        except ValueError:
            label = "分享"
        if label == "引用":
            ref = _find_text(root, ".//refermsg/displayname")
            ref_content = _find_text(root, ".//refermsg/content")
            quote = f"「{ref}: {ref_content[:40]}」" if ref else ""
            return f"{title} {quote}".strip()
        if label == "转账":
            fee = _find_text(root, ".//wcpayinfo/feedesc")
            return f"[转账] {fee}".rstrip()
        url = _find_text(root, ".//appmsg/url")
        text = f"[{label}] {title}".rstrip()
        if label == "链接" and url:
            text += f" ({url})"
        return text
    return TYPE_LABELS.get(msg_type, f"[未知消息类型 {msg_type}]")


def split_group_sender(content: str) -> tuple[str, str]:
    """iOS / Android 群聊收到的消息形如 'wxid_xxx:\\n正文'，拆出发送者。"""
    if content:
        m = re.match(r"^([A-Za-z0-9_\-@.]+):\n", content)
        if m:
            return m.group(1), content[m.end():]
    return "", content
