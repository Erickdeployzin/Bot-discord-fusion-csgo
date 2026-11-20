from datetime import datetime, timezone
from typing import Optional

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logger import logger
from database.models.guild import GuildConfig, GuildLogChannel, LogCategory


CATEGORY_METADATA: dict[LogCategory, tuple[str, discord.Color]] = {
    LogCategory.TICKETS: ("🎫 Log de Tickets", discord.Color.blurple()),
    LogCategory.SUPPORT: ("🎧 Log de Atendimentos", discord.Color.teal()),
    LogCategory.SALES: ("🛒 Log de Compras", discord.Color.gold()),
    LogCategory.PAYMENTS: ("💳 Log de Pagamentos", discord.Color.green()),
    LogCategory.REFUNDS: ("↩️ Log de Reembolsos", discord.Color.orange()),
    LogCategory.DELIVERIES: ("📦 Log de Entregas", discord.Color.brand_green()),
    LogCategory.VIPS: ("👑 Log de VIPs", discord.Color.purple()),
    LogCategory.PRODUCTS: ("🏷️ Log de Produtos", discord.Color.blue()),
    LogCategory.SETTINGS: ("⚙️ Log de Configurações", discord.Color.light_grey()),
    LogCategory.ADMIN_ACTIONS: ("🛡️ Log de Ações Administrativas", discord.Color.dark_gold()),
    LogCategory.ERRORS: ("🚨 Log de Erros do Sistema", discord.Color.red()),
}


class LogService:
    """Despachante centralizado dos 11 canais de logs independentes por servidor."""

    @staticmethod
    async def dispatch(
        bot: discord.Client,
        session: AsyncSession,
        guild_id: int,
        category: LogCategory,
        *,
        title: str,
        description: str,
        actor: Optional[discord.abc.User] = None,
        fields: Optional[list[tuple[str, str, bool]]] = None,
        file: Optional[discord.File] = None,
    ) -> bool:
        stmt = select(GuildLogChannel).where(
            GuildLogChannel.guild_id == guild_id,
            GuildLogChannel.category == category,
            GuildLogChannel.is_enabled.is_(True),
        )
        log_cfg = (await session.execute(stmt)).scalar_one_or_none()
        if not log_cfg:
            return False

        guild = bot.get_guild(guild_id)
        if not guild:
            return False

        channel = guild.get_channel(log_cfg.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        guild_cfg = await session.get(GuildConfig, guild_id)
        cat_label, color = CATEGORY_METADATA.get(
            category,
            ("📋 Log do Sistema", discord.Color.blurple()),
        )

        embed = discord.Embed(
            title=f"{cat_label} • {title}",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        if actor:
            embed.set_author(
                name=f"{actor} ({actor.id})",
                icon_url=actor.display_avatar.url if actor.display_avatar else None,
            )

        if fields:
            for name, value, inline in fields[:25]:
                embed.add_field(name=name, value=value[:1024], inline=inline)

        footer = guild_cfg.footer_text if guild_cfg else "FUSION • Sistema de Auditoria"
        embed.set_footer(text=footer)

        try:
            if file is not None:
                await channel.send(embed=embed, file=file)
            else:
                await channel.send(embed=embed)
            return True
        except discord.Forbidden:
            logger.warning(f"[Guild {guild_id}] Sem permissão para enviar log em #{channel.id} ({category.value})")
            return False
        except Exception as exc:
            logger.exception(f"[Guild {guild_id}] Falha ao enviar log ({category.value}): {exc}")
            return False