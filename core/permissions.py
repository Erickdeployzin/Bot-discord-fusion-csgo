import time
from collections import defaultdict
from typing import Dict, Tuple

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from database.models.guild import BotModule, GuildModulePermission


class RateLimiter:
    """Proteção contra spam e abuso em interações e comandos."""

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[int, str], list[float]] = defaultdict(list)

    def check(self, user_id: int, action: str, max_calls: int, window_seconds: float) -> Tuple[bool, float]:
        now = time.monotonic()
        key = (user_id, action)
        timestamps = [ts for ts in self._buckets[key] if (now - ts) < window_seconds]
        if len(timestamps) >= max_calls:
            retry_after = window_seconds - (now - timestamps[0])
            self._buckets[key] = timestamps
            return False, max(0.1, round(retry_after, 1))
        timestamps.append(now)
        self._buckets[key] = timestamps
        return True, 0.0


rate_limiter = RateLimiter()


async def has_module_permission(
    session: AsyncSession,
    member: discord.Member,
    module: BotModule,
) -> bool:
    """
    Verifica hierarquia de permissões:
    1. Dono do bot (OWNER_IDS) ou Dono do servidor Discord
    2. Permissão Administrator do Discord
    3. Permissões explícitas configuradas no painel para o módulo (por usuário ou cargo)
    """
    settings = get_settings()
    if member.id in settings.owner_ids:
        return True
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator:
        return True

    stmt = select(GuildModulePermission).where(
        GuildModulePermission.guild_id == member.guild.id,
        GuildModulePermission.module == module,
    )
    rules = (await session.execute(stmt)).scalars().all()
    if not rules:
        return False

    member_role_ids = {role.id for role in member.roles}

    # Negação explícita tem precedência sobre permissão
    for rule in rules:
        if rule.target_type == "user" and rule.target_id == member.id and not rule.allowed:
            return False
        if rule.target_type == "role" and rule.target_id in member_role_ids and not rule.allowed:
            return False

    for rule in rules:
        if rule.target_type == "user" and rule.target_id == member.id and rule.allowed:
            return True
        if rule.target_type == "role" and rule.target_id in member_role_ids and rule.allowed:
            return True

    return False