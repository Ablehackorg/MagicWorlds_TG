# post_tg/utils/tg_links.py
import re
from typing import Tuple, Optional

_TME_C = re.compile(r"https?://t\.me/c/(\d+)/(\d+)")
_TME_USER = re.compile(r"https?://t\.me/([A-Za-z0-9_]+)/(\d+)")
_PLAIN_USER = re.compile(r"^@?([A-Za-z0-9_]+)/(\d+)$")

def parse_post_link(link: str) -> Tuple[Optional[int], Optional[str], int]:
    """
    Возвращает (chat_id (int, если t.me/c), username (str, если публичный), message_id).
    Для каналов преобразует ID в формат -100xxxxxxxxxx
    """
    link = (link or "").strip()

    m = _TME_C.match(link)
    if m:
        inner_id = int(m.group(1))
        mid = int(m.group(2))
        # Правильное преобразование для каналов: -100 + inner_id
        chat_id = int(f"-100{inner_id}")
        return (chat_id, None, mid)

    m = _TME_USER.match(link)
    if m:
        return (None, m.group(1), int(m.group(2)))

    m = _PLAIN_USER.match(link)
    if m:
        return (None, m.group(1), int(m.group(2)))

    raise ValueError(f"Unsupported Telegram post link: {link}")