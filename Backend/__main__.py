from asyncio import get_event_loop, sleep as asleep
import asyncio
import logging
from traceback import format_exc
from pyrogram import idle
from Backend import __version__, db
from Backend.helper.pinger import ping
from Backend.logger import LOGGER
from Backend.fastapi import server
from Backend.helper.pyro import restart_notification, setup_bot_commands
from Backend.pyrofork.bot import Helper, StreamBot
from Backend.pyrofork.clients import initialize_clients
from Backend.helper.link_checker import DeadLinkChecker
from Backend.fastapi.main import app
from Backend.config import Telegram

loop = get_event_loop()

async def start_services():
    try:
        LOGGER.info(f"Initializing Telegram-Stremio v-{__version__}")
        await asleep(1.2)
        
        await db.connect()
        await asleep(1.2)
        
        await StreamBot.start()
        StreamBot.username = StreamBot.me.username
        LOGGER.info(f"Bot Client : [@{StreamBot.username}]")
        await asleep(1.2)

        await Helper.start()
        Helper.username = Helper.me.username
        LOGGER.info(f"Helper Bot Client : [@{Helper.username}]")
        await asleep(1.2)

        LOGGER.info("Initializing Multi Clients...")
        await initialize_clients()
        await asleep(2)

        await setup_bot_commands(StreamBot)
        await asleep(2)

        LOGGER.info('Initializing Telegram-Stremio Web Server...')
        await restart_notification()
        loop.create_task(server.serve())
        loop.create_task(ping())
        
        # Start the background Dead Link Checker
        link_checker_task = DeadLinkChecker(db, app, check_interval_hours=24)
        loop.create_task(link_checker_task.start())

        if Telegram.NOTIFY_ENABLED:
            from Backend.helper.notify_bot import notify_loop
            loop.create_task(notify_loop(StreamBot))
            LOGGER.info("Notify Bot Task Started.")
        
        # Start Subscription Background Task
        if Telegram.SUBSCRIPTION:
            from Backend.helper.subscription_checker import subscription_checker_loop
            loop.create_task(subscription_checker_loop(StreamBot))
            LOGGER.info("Subscription Checker Task Started.")
        
        LOGGER.info("Telegram-Stremio Started Successfully!")
        await idle()
    except Exception:
        LOGGER.error("Error during startup:\n" + format_exc())

async def stop_services():
    try:
        LOGGER.info("Stopping services...")

        pending_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in pending_tasks:
            task.cancel()
        
        await asyncio.gather(*pending_tasks, return_exceptions=True)

        await StreamBot.stop()
        await Helper.stop()

        await db.disconnect()
        
        LOGGER.info("Services stopped successfully.")
    except Exception:
        LOGGER.error("Error during shutdown:\n" + format_exc())

if __name__ == '__main__':
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        LOGGER.info('Service Stopping...')
    except Exception:
        LOGGER.error(format_exc())
    finally:
        loop.run_until_complete(stop_services())
        loop.stop()
        logging.shutdown()  
