from datetime import datetime, timezone
from typing import Optional

import discord
from fastapi import FastAPI

from api.routes.csgo import router as csgo_router
from api.routes.laravel import router as laravel_router
from api.routes.mercadopago import router as mercadopago_router
from core.config import get_settings


def create_fastapi_app(bot: Optional[discord.Client] = None) -> FastAPI:
    """Cria a instância FastAPI compartilhando o mesmo event loop do bot Discord."""
    settings = get_settings()
    app = FastAPI(
        title="FUSION BOT",
        version="1.0.0",
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url=None,
    )
    app.state.bot = bot
    app.include_router(mercadopago_router)
    app.include_router(csgo_router)
    app.include_router(laravel_router)

    @app.get("/health", tags=["Sistema"])
    async def healthcheck() -> dict:
        discord_ready = bool(bot and bot.is_ready())
        return {
            "status": "ok",
            "service": settings.app_name,
            "discord_connected": discord_ready,
            "guilds_count": len(bot.guilds) if discord_ready and bot else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return app