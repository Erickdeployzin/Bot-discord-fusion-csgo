import traceback

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from bot.embeds.factory import EmbedFactory
from bot.views.panel_views import ensure_guild_config
from core.database import get_db_session
from core.logger import logger
from database.models.automations import AutomatedMessage, AutomationTrigger
from database.models.guild import GuildConfig, LogCategory
from modules.logs.service import LogService
from utils.helpers import parse_hex_color, render_template_variables


class GuildEventsCog(commands.Cog):
    """Gerencia entrada/saída de membros, auto-role, automações de evento e erros globais."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bot.tree.on_error = self.on_app_command_error

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await ensure_guild_config(guild)
        logger.info(f"Bot adicionado ao servidor: {guild.name} ({guild.id})")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._handle_member_event(member, AutomationTrigger.MEMBER_JOIN, apply_autorole=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._handle_member_event(member, AutomationTrigger.MEMBER_LEAVE, apply_autorole=False)

    async def _handle_member_event(
        self,
        member: discord.Member,
        trigger: AutomationTrigger,
        *,
        apply_autorole: bool,
    ) -> None:
        guild = member.guild
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, guild.id)
            if not cfg or not cfg.is_active:
                return

            if apply_autorole and cfg.auto_role_id:
                role = guild.get_role(cfg.auto_role_id)
                if role:
                    try:
                        await member.add_roles(role, reason="Auto-Role FUSION BOT")
                    except discord.Forbidden:
                        logger.warning(f"[Guild {guild.id}] Sem permissão para atribuir Auto-Role {role.id}")

            stmt = select(AutomatedMessage).where(
                AutomatedMessage.guild_id == guild.id,
                AutomatedMessage.trigger_type == trigger,
                AutomatedMessage.is_active.is_(True),
            )
            automations = (await session.execute(stmt)).scalars().all()

            for auto_msg in automations:
                if not auto_msg.channel_id:
                    continue
                channel = guild.get_channel(auto_msg.channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue

                content = render_template_variables(
                    auto_msg.content_text,
                    guild=guild,
                    user=member,
                    tz_name=cfg.timezone,
                )
                title = render_template_variables(
                    auto_msg.embed_title,
                    guild=guild,
                    user=member,
                    tz_name=cfg.timezone,
                )
                desc = render_template_variables(
                    auto_msg.embed_description,
                    guild=guild,
                    user=member,
                    tz_name=cfg.timezone,
                )

                embed = EmbedFactory.build(
                    cfg,
                    title=title or auto_msg.name,
                    description=desc,
                    color_override=parse_hex_color(auto_msg.embed_color_hex),
                    image_url=auto_msg.embed_image_url,
                    thumbnail_url=auto_msg.embed_thumbnail_url or member.display_avatar.url,
                )
                if auto_msg.embed_footer_text:
                    embed.set_footer(text=auto_msg.embed_footer_text)

                try:
                    await channel.send(content=content or None, embed=embed)
                except discord.Forbidden:
                    logger.warning(f"[Guild {guild.id}] Sem permissão para enviar automação #{auto_msg.id}")

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        logger.error(f"Erro em Slash Command: {error}", exc_info=error)
        if interaction.guild:
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-900:]
            async with get_db_session() as session:
                await LogService.dispatch(
                    self.bot,
                    session,
                    interaction.guild.id,
                    LogCategory.ERRORS,
                    title="Falha em Comando Slash",
                    description=f"```py\n{tb}\n```",
                    actor=interaction.user,
                )

        msg = "❌ Ocorreu um erro interno ao executar este comando. A equipe técnica foi notificada."
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildEventsCog(bot))