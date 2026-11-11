import asyncio
import time
from collections import deque

from Backend import db
from Backend.helper.custom_dl import ACTIVE_STREAMS, RECENT_STREAMS
from Backend.pyrofork.bot import client_avg_mbps, work_loads

MIB = 1024 * 1024


#----- Show Cloudflare streams like local ones. Each Worker client reports its live streams every
#----- ~15s; they go into ACTIVE_STREAMS (dashboard, user activity, bot workloads) and, once they
#----- stop being reported, into RECENT_STREAMS and the stream history.
async def apply_report(member: str, client_index, dc, streams: list) -> None:
    from Backend.fastapi.routes.stream_routes import _lookup_title

    now = time.time()
    seen = set()
    for s in streams:
        sid = f"cf-{s.get('id')}"
        seen.add(sid)
        last_ts = (s.get("last") or s.get("since") or now * 1000) / 1000
        total = int(s.get("bytes") or 0)
        entry = ACTIVE_STREAMS.get(sid)
        if entry is None:
            token = str(s.get("token") or "")
            token_data = await db.get_api_token(token) if token else None
            entry = {
                "stream_id": sid,
                "msg_id": s.get("msg"),
                "chat_id": s.get("chat"),
                "dc_id": dc,
                "client_index": client_index,
                "start_ts": (s.get("since") or now * 1000) / 1000,
                "last_ts": last_ts,
                "total_bytes": 0,
                "avg_mbps": 0.0,
                "instant_mbps": 0.0,
                "peak_mbps": 0.0,
                "recent_measurements": deque(maxlen=3),
                "status": "active",
                "part_count": 0,
                "prefetch": 0,
                "meta": {
                    "title": await _lookup_title(s.get("file"), s.get("name") or ""),
                    "file_name": s.get("name"),
                    "user_name": token_data.get("name", "Unknown") if token_data else "Unknown",
                    "token": token,
                    "client_host": s.get("ip"),
                    "source": "cloudflare",
                    "cf_member": member,
                },
            }
            ACTIVE_STREAMS[sid] = entry
            if client_index in work_loads:
                work_loads[client_index] += 1
        elapsed = last_ts - entry["last_ts"]
        if elapsed > 0:
            entry["instant_mbps"] = (total - entry["total_bytes"]) / MIB / elapsed
            entry["peak_mbps"] = max(entry["peak_mbps"], entry["instant_mbps"])
        entry["total_bytes"] = total
        entry["last_ts"] = last_ts
        duration = last_ts - entry["start_ts"]
        entry["avg_mbps"] = total / MIB / duration if duration > 0 else 0.0

    #----- Streams this client no longer reports have ended
    for sid, entry in list(ACTIVE_STREAMS.items()):
        if sid.startswith("cf-") and entry["meta"].get("cf_member") == member and sid not in seen:
            _finish(sid, entry)


def _finish(sid: str, entry: dict) -> None:
    ACTIVE_STREAMS.pop(sid, None)
    entry.update({
        "end_ts": entry["last_ts"],
        "duration": max(0.0, entry["last_ts"] - entry["start_ts"]),
        "status": "finished",
    })
    idx = entry.get("client_index")
    if idx in work_loads:
        work_loads[idx] = max(0, work_loads[idx] - 1)
        prev = client_avg_mbps.get(idx, 0.0)
        client_avg_mbps[idx] = entry["avg_mbps"] if prev == 0.0 else 0.5 * prev + 0.5 * entry["avg_mbps"]
    RECENT_STREAMS.appendleft(entry)
    asyncio.create_task(db.log_stream_stats(entry))
