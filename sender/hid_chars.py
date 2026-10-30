"""
HID 可输出字符集 —— 与固件里的 isSupportedChar() 严格一一对应。

板子拿到文本后逐字符查表按 US 键盘 scancode 输出，表外的字符直接跳过，
所以发送端必须提前拦下来，否则用户看到的是「发出去了、但目标机上少了字」。

规则（改这里之前先看 esp32_hid_key_ble.ino 的 isSupportedChar）：
    A-Z a-z 0-9
    ! @ # $ % ^ & * ( ) _ + - = [ ] { } ; ' : " , . / < > ? \\ | ` ~
    空格、换行 \\n

Note:
    换行统一成 \\n 之后再校验。CR 一律视为不支持 —— 板子收到 \\r 会多敲一次
    回车，之前就是靠 normalize 把它清掉的。
"""

from typing import Dict, NamedTuple, Optional

_PUNCT = set("!@#$%^&*()_+-=[]{};':\",./<>?\\|`~ ")


class ScanResult(NamedTuple):
    count: int                    # 不支持字符总数
    line: int                     # 首个不支持字符所在行（1 起，无问题时为 0）
    col: int                      # 所在列（1 起）
    char: Optional[str]           # 首个不支持字符本身
    chars: Dict[str, int]         # 字符 → 出现次数，用于展示

    @property
    def ok(self) -> bool:
        return self.count == 0


def is_supported(ch: str) -> bool:
    if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9"):
        return True
    return ch in _PUNCT or ch == "\n"


def normalize_newlines(s: str) -> str:
    """CRLF / CR 统一成 LF，和固件存档时的处理保持一致。"""
    if "\r" not in s:
        return s
    return s.replace("\r\n", "\n").replace("\r", "\n")


def scan(text: str) -> ScanResult:
    """扫描不支持字符。调用前请先 normalize_newlines。"""
    cache: Dict[str, int] = {}
    count = 0
    first = None
    line, col = 1, 1
    for ch in text:
        if ch == "\n":
            line += 1
            col = 1
            continue
        if not is_supported(ch):
            count += 1
            cache[ch] = cache.get(ch, 0) + 1
            if first is None:
                first = (line, col, ch)
        col += 1

    if first is None:
        return ScanResult(0, 0, 0, None, {})
    return ScanResult(count, first[0], first[1], first[2], cache)


def strip_unsupported(text: str) -> str:
    return "".join(ch for ch in text if ch == "\n" or is_supported(ch))


def show_char(ch: str) -> str:
    """把字符变成人能看懂的样子：控制符单独说明，其余带上码位。"""
    if ch == "\t":
        return "Tab 制表符"
    if ch == "\n":
        return "换行"
    code = ord(ch)
    if code < 32 or code == 127:
        return f"控制符 U+{code:04X}"
    return f"{ch}（U+{code:04X}）"


def describe(chars: Dict[str, int], limit: int = 5) -> str:
    """把 {字符: 次数} 拼成一行提示，多的用「等 N 种」收尾。"""
    items = sorted(chars.items(), key=lambda kv: (-kv[1], ord(kv[0])))
    parts = [f"{show_char(ch)} ×{n}" for ch, n in items[:limit]]
    extra = len(items) - limit
    if extra > 0:
        parts.append(f"另 {extra} 种")
    return "、".join(parts)
