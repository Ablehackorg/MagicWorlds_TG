# text_entities.py
from typing import List, Tuple
from telethon.tl.types import TypeMessageEntity  # алиас для любых Entity (Bold, TextUrl, и т.д.)

def concat_with_entities(parts: List[Tuple[str, List[TypeMessageEntity]]]) -> tuple[str, List[TypeMessageEntity]]:
    """
    parts: [(text, entities)], где entities — список любых MessageEntity*.
    Возвращает объединённый текст и список сущностей с корректно сдвинутыми offset.
    """
    buf: List[str] = []
    all_entities: List[TypeMessageEntity] = []
    offset_acc = 0

    for i, (txt, ents) in enumerate(parts):
        txt = txt or ""
        if i:
            sep = "\n\n"
            buf.append(sep)
            offset_acc += len(sep)

        buf.append(txt)
        tlen = len(txt)

        for e in (ents or []):
            # копируем сущность, сдвигая offset. Остальные поля (например, url у TextUrl) сохраняем.
            kwargs = {}
            for k in getattr(e, "__slots__", []):
                if k in ("offset", "length"):
                    continue
                kwargs[k] = getattr(e, k)
            e2 = e.__class__(offset=e.offset + offset_acc, length=e.length, **kwargs)
            all_entities.append(e2)

        offset_acc += tlen

    return "".join(buf), all_entities
