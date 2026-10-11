"""极简 protobuf 解码，只够读取微信数据库中的几个 blob 字段。"""

from __future__ import annotations


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
    raise ValueError("varint 截断")


def parse_fields(buf: bytes) -> list[tuple[int, int | bytes]]:
    """返回 [(字段号, 值)]；varint 字段值为 int，长度分隔字段值为 bytes。解析出错时返回已解析部分。"""
    out: list[tuple[int, int | bytes]] = []
    pos = 0
    try:
        while pos < len(buf):
            tag, pos = _read_varint(buf, pos)
            field_no, wire = tag >> 3, tag & 7
            if wire == 0:
                value, pos = _read_varint(buf, pos)
                out.append((field_no, value))
            elif wire == 2:
                length, pos = _read_varint(buf, pos)
                if pos + length > len(buf):
                    break
                out.append((field_no, buf[pos:pos + length]))
                pos += length
            elif wire == 1:
                pos += 8
            elif wire == 5:
                pos += 4
            else:
                break
    except ValueError:
        pass
    return out


def first_strings(buf: bytes) -> dict[int, str]:
    """每个字段号取第一个长度分隔值，按 UTF-8 解码。"""
    out: dict[int, str] = {}
    for no, value in parse_fields(buf):
        if isinstance(value, bytes) and no not in out:
            out[no] = value.decode("utf-8", "replace")
    return out
