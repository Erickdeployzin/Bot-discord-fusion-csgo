import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.embeds.factory import EmbedFactory
from core.database import get_db_session
from core.permissions import has_module_permission
from database.models.guild import BotModule, GuildConfig
from database.models.vip import UserSteamLink, UserVip, VipStatus
from modules.vip.service import VipService


class VipCog(commands.Cog):
    """Comandos de consulta VIP, vínculo SteamID para CS:GO Legacy e administração."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="meu-vip", description="Consulta seus planos VIP ativos, validade e conta Steam vinculada.")
    @app_commands.guild_only()
    async def my_vip(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            vips = (
                await session.execute(
                    select(UserVip)
                    .where(
                        UserVip.guild_id == interaction.guild.id,
                        UserVip.user_id == interaction.user.id,
                        UserVip.status == VipStatus.ACTIVE,
                    )
                    .options(selectinload(UserVip.plan))
                )
            ).scalars().all()

            steam_link = (
                await session.execute(
                    select(UserSteamLink).where(UserSteamLink.discord_user_id == interaction.user.id)
                )
            ).scalar_one_or_none()

            if not vips:
                desc = (
                    "Você não possui nenhum plano VIP ativo neste servidor no momento.\n"
                    "Use `/loja` para conhecer os planos **VIP Prata**, **VIP Diamante** e **VIP Permanente**!"
                )
            else:
                lines = []
                for uv in vips:
                    exp_txt = (
                        f"Expira <t:{int(uv.expires_at.timestamp())}:R> (`{uv.expires_at.strftime('%d/%m/%Y')}`)"
                        if uv.expires_at
                        else "**Permanente**"
                    )
                    benefits = ", ".join(uv.plan.benefits) if uv.plan.benefits else "Benefícios exclusivos FUSION"
                    lines.append(f"• 👑 **{uv.plan.name}** — {exp_txt}\n  *Benefícios:* {benefits}")
                desc = "\n\n".join(lines)

            embed = EmbedFactory.build(
                cfg,
                title=f"👑 Status VIP • {interaction.user.display_name}",
                description=desc,
                thumbnail_url=interaction.user.display_avatar.url,
            )
            embed.add_field(
                name="Conta Steam (CS:GO Legacy)",
                value=(
                    f"`{steam_link.steam_id64}` (`{steam_link.steam_id2}`)"
                    if steam_link
                    else "`Não vinculada` — Use `/vincular-steam`"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="vincular-steam",
        description="Vincula seu SteamID para ativação automática do VIP nos servidores CS:GO Legacy.",
    )
    @app_commands.describe(steam_id="Seu SteamID64 (ex: 76561198000000000) ou SteamID2 (ex: STEAM_1:0:12345)")
    async def link_steam(self, interaction: discord.Interaction, steam_id: str) -> None:
        async with get_db_session() as session:
            ok, msg, _ = await VipService.link_steam_account(session, interaction.user.id, steam_id)
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(
        name="vip-revogar",
        description="[Admin] Revoga manualmente uma assinatura VIP ativa de um membro.",
    )
    @app_commands.describe(
        membro="Membro que terá o VIP revogado",
        motivo="Motivo da revogação",
    )
    @app_commands.guild_only()
    async def revoke_vip_cmd(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Revogação administrativa",
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.VIPS):
                await interaction.response.send_message("🚫 Sem permissão para gerenciar VIPs.", ephemeral=True)
                return

            active_vip = (
                await session.execute(
                    select(UserVip).where(
                        UserVip.guild_id == interaction.guild.id,
                        UserVip.user_id == membro.id,
                        UserVip.status == VipStatus.ACTIVE,
                    )
                )
            ).scalars().first()

            if not active_vip:
                await interaction.response.send_message(
                    f"⚠️ {membro.mention} não possui nenhum VIP ativo no momento.",
                    ephemeral=True,
                )
                return

            _, msg = await VipService.revoke_vip(
                interaction.client,
                session,
                interaction.guild,
                active_vip.id,
                actor=interaction.user,
                reason=motivo,
                is_expired=False,
            )

        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VipCog(bot))