import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Configura variáveis de ambiente obrigatórias antes de carregar core.config
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DISCORD_TOKEN", "MTIzNDU2Nzg5MDEyMzQ1Njc4.TEST_TOKEN_PARA_SUITE_DE_TESTES")
os.environ.setdefault("DISCORD_CLIENT_ID", "123456789012345678")
os.environ.setdefault("OWNER_IDS", "111222333444555666")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
os.environ.setdefault("LARAVEL_API_KEY", "test_laravel_api_key_123456")
os.environ.setdefault("SOURCEMOD_API_KEY", "test_sourcemod_api_key_123456")

from database.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Cria banco SQLite assíncrono em memória isolado para cada caso de teste."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.commit()

    await engine.dispose()


@pytest.fixture
def mock_discord_env() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Simula Bot, Guild e Member do discord.py com métodos assíncronos."""
    guild = MagicMock(spec=discord.Guild)
    guild.id = 987654321012345678
    guild.name = "FUSION CS:GO Legacy"
    guild.owner_id = 111222333444555666
    guild.member_count = 350

    vip_role = MagicMock(spec=discord.Role)
    vip_role.id = 555666777888999000
    vip_role.name = "VIP Diamante"
    vip_role.mention = "<@&555666777888999000>"

    customer_role = MagicMock(spec=discord.Role)
    customer_role.id = 444555666777888999
    customer_role.name = "Cliente FUSION"
    customer_role.mention = "<@&444555666777888999>"

    roles_map = {vip_role.id: vip_role, customer_role.id: customer_role}
    guild.get_role = MagicMock(side_effect=lambda rid: roles_map.get(rid))

    member = MagicMock(spec=discord.Member)
    member.id = 222333444555666777
    member.name = "erik_csgo"
    member.display_name = "Erik"
    member.mention = "<@222333444555666777>"
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.send = AsyncMock()

    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_channel = MagicMock(return_value=None)

    bot = MagicMock(spec=discord.Client)
    bot.get_guild = MagicMock(return_value=guild)
    bot.fetch_user = AsyncMock(return_value=member)
    bot.is_ready = MagicMock(return_value=True)
    bot.guilds = [guild]

    return bot, guild, member