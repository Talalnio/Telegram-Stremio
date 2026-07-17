from asyncio import gather, create_task, sleep, Semaphore
from pyrogram import Client
from pyrogram.errors import FloodWait
from Backend.logger import LOGGER
from Backend.config import Telegram
from Backend.pyrofork.bot import multi_clients, work_loads, StreamBot, client_dc_map
from os import environ

class TokenParser:
    @staticmethod
    def parse_from_env():
        tokens = {
            c + 1: t
            for c, (_, t) in enumerate(
                filter(
                    lambda n: n[0].startswith("MULTI_TOKEN"),
                    sorted(environ.items())
                )
            )
        }
        return tokens

_start_sem = Semaphore(max(1, Telegram.MULTI_CLIENT_START_MAX_CONCURRENCY))


async def _delayed_start(client_id: int, token: str, wait_seconds: int):
    await sleep(max(1, int(wait_seconds)))
    result = await start_client(client_id, token)
    if result:
        cid, client = result
        multi_clients[cid] = client
        if cid not in work_loads:
            work_loads[cid] = 0


async def start_client(client_id, token):
    try:
        async with _start_sem:
            LOGGER.info(f"Starting - Bot Client {client_id}")
            client = await Client(
                name=str(client_id),
                api_id=Telegram.API_ID,
                api_hash=Telegram.API_HASH,
                bot_token=token,
                sleep_threshold=100,
                no_updates=True,
                in_memory=True
            ).start()
        
        try:
            client_dc = await client.storage.dc_id()
            client_dc_map[client_id] = client_dc
            LOGGER.info(f"Client {client_id} connected to DC {client_dc}")
        except Exception as e:
            LOGGER.warning(f"Could not get DC for Client {client_id}: {e}")
            client_dc_map[client_id] = None
        
        work_loads[client_id] = 0
        return client_id, client
    except FloodWait as e:
        wait_seconds = int(getattr(e, "value", 0) or 0)
        LOGGER.warning(f"FloodWait while starting Client {client_id}: {wait_seconds}s required")
        if wait_seconds <= 60:
            await sleep(max(1, wait_seconds))
            return await start_client(client_id, token)

        if wait_seconds <= Telegram.MULTI_CLIENT_FLOODWAIT_MAX_SCHEDULE:
            create_task(_delayed_start(client_id, token, wait_seconds))
            LOGGER.warning(f"Scheduled retry for Client {client_id} in {wait_seconds}s")
        return None
    except Exception as e:
        LOGGER.error(f"Failed to start Client - {client_id} Error: {e}", exc_info=True)
        return None

async def initialize_clients():
    multi_clients[0], work_loads[0] = StreamBot, 0
    
    try:
        main_dc = await StreamBot.storage.dc_id()
        client_dc_map[0] = main_dc
        LOGGER.info(f"Main StreamBot connected to DC {main_dc}")
    except Exception as e:
        LOGGER.warning(f"Could not get DC for StreamBot: {e}")
        client_dc_map[0] = None
    
    all_tokens = TokenParser.parse_from_env()
    if not all_tokens:
        LOGGER.info("No additional Bot Clients found, Using default client")
        return

    gap = max(0, Telegram.MULTI_CLIENT_START_GAP_SECONDS)
    tasks = []
    for i, token in all_tokens.items():
        tasks.append(create_task(start_client(i, token)))
        if gap:
            await sleep(gap)
    clients = await gather(*tasks)
    ok = [c for c in clients if c]
    for cid, client in ok:
        multi_clients[cid] = client
        if cid not in work_loads:
            work_loads[cid] = 0

    if len(multi_clients) != 1:
        LOGGER.info(f"Multi-Client Mode Enabled with {len(multi_clients)} clients")
        LOGGER.info(f"DC Distribution: {client_dc_map}")
    else:
        LOGGER.info("No additional clients were initialized, using default client")
