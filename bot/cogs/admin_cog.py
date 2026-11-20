import discord
from discord import app_commands
from discord.ext import commands

from bot.views.panel_views import ensure_guild_config, render_category_screen
from core.database import get_db_session
from core.permissions import has_module_permission, rate_limiter
from database.models.guild import BotModule


class AdminCog(commands.Cog):
    """Comandos administrativos principais do FUSION BOT."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="painel",
        description="Abre a Central Administrativa completa da FUSION (15 categorias).",
    )
    @app_commands.guild_only()
    async def panel_command(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        allowed_rl, retry = rate_limiter.check(interaction.user.id, "cmd_painel", max_calls=3, window_seconds=10.0)
        if not allowed_rl:
            await interaction.response.send_message(
                f"⏳ Aguarde `{retry}s` antes de abrir o painel novamente.",
                ephemeral=True,
            )
            return

        await ensure_guild_config(interaction.guild)

        async with get_db_session() as session:
            has_perm = await has_module_permission(session, interaction.user, BotModule.ADMIN_PANEL)

        if not has_perm:
            await interaction.response.send_message(
                "🚫 Você não possui permissão para acessar o `/painel` administrativo.",
                ephemeral=True,
            )
            return

        await render_category_screen(interaction, "home", is_initial=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))