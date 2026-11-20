import asyncio
import signal
from typing import List

import discord
import uvicorn
from discord.ext import commands

from api.app import create_fastapi_app
from core.config import get_settings
from core.database import engine
from core.logger import logger, setup_logging
from tasks.scheduler import FusionScheduler


INITIAL_EXTENSIONS: List[str] = [
    "bot.cogs.admin_cog",
    "bot.events.guild_events",
    "modules.tickets.cog",
    "modules.sales.cog",
    "modules.vip.cog",
    "modules.csgo.cog",
]


class FusionBot(commands.Bot):
    """Cliente Discord da FUSION."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.scheduler = FusionScheduler(self)

    async def setup_hook(self) -> None:
        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)
            logger.info(f"Extensão carregada: {ext}")

        synced = await self.tree.sync()
        logger.info(f"Slash commands sincronizados globalmente: {len(synced)}")
        self.scheduler.start()

    async def close(self) -> None:
        self.scheduler.shutdown()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info(f"FUSION BOT conectado como {self.user} (ID: {self.user.id}) | Servidores: {len(self.guilds)}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="FUSION CS:GO • /painel",
            )
        )


async def run_services() -> None:
    settings = get_settings()
    setup_logging(settings.logs_dir, settings.app_env)

    bot = FusionBot()
    fastapi_app = create_fastapi_app(bot)

    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning",
        loop="asyncio",
    )
    api_server = uvicorn.Server(uvicorn_config)

    loop = asyncio.get_running_loop()

    def _shutdown_handler() -> None:
        logger.info("Sinal de encerramento recebido. Finalizando FUSION BOT e API...")
        api_server.should_exit = True
        asyncio.create_task(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown_handler)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(
            bot.start(settings.discord_token),
            api_server.serve(),
        )
    finally:
        if not bot.is_closed():
            await bot.close()
        await engine.dispose()
        logger.info("Recursos de banco de dados e conexões encerrados com segurança.")


if __name__ == "__main__":
    asyncio.run(run_services())