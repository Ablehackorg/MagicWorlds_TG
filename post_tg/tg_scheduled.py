from telethon import functions, types
from typing import List, Dict

from entity_resolver import ensure_peer

MATCH_TOLERANCE_SEC = 60  # допустимая погрешность по времени


async def list_scheduled_entity(client, peer_entity) -> list:
    res = await client(functions.messages.GetScheduledHistoryRequest(peer=peer_entity, hash=0))
    return list(res.messages or [])



def map_by_time(msgs: List[types.Message]) -> Dict[int, list[types.Message]]:
    out: Dict[int, list[types.Message]] = {}
    for m in msgs:
        when = getattr(m, "date", None)
        if not when:
            continue
        ts = int(when.timestamp())
        out.setdefault(ts, []).append(m)
    return out

async def delete_scheduled_by_ids_entity(client, peer_entity, ids: list[int]) -> None:
    if not ids:
        return
    await client(functions.messages.DeleteScheduledMessagesRequest(peer=peer_entity, id=ids))
