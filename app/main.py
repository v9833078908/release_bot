import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from app.bot import build_dispatcher
from app.config import get_settings
from app.github import GitHub
from app.prod import fetch_prod_sha
from app.scheduler import build_scheduler
from app.store import Store

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings = get_settings()
    bot = Bot(settings.release_bot_token)
    bot._gh = GitHub(settings.github_token, settings.github_repo)
    bot._get_prod_sha = lambda: fetch_prod_sha(settings.prod_version_url)
    store = Store(settings.db_path, settings.initial_marker_sha)

    await bot.delete_webhook(drop_pending_updates=False)  # ensure polling, no webhook conflict

    await bot.set_my_commands([
        BotCommand(command="release_draft", description="Черновик релиз-поста (по проду)"),
        BotCommand(command="preview", description="Превью недеплоенных изменений (main)"),
        BotCommand(command="status", description="Маркер и статус черновика"),
        BotCommand(command="redraft", description="Пересобрать черновик с заметкой"),
    ])

    dp = build_dispatcher(bot, store, settings)
    scheduler = build_scheduler(bot, store, settings)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
