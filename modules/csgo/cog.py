import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import desc, select

from bot.embeds.factory import EmbedFactory
from core.database import get_db_session
from database.models.csgo import CsgoMatchHistory, CsgoServer
from database.models.guild import GuildConfig
from modules.csgo.service import CsgoService


class CsgoCog(commands.Cog):
    """Comandos de consulta em tempo real dos servidores CS:GO Legacy e estatísticas MIX."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="servidores",
        description="Consulta o status ao vivo (UDP A2S), mapa e jogadores dos servidores CS:GO Legacy.",
    )
    @app_commands.guild_only()
    async def servers_status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.defer(ephemeral=False)

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            servers = (
                await session.execute(
                    select(CsgoServer).where(
                        CsgoServer.guild_id == interaction.guild.id,
                        CsgoServer.is_active.is_(True),
                    )
                )
            ).scalars().all()

            if not servers:
                await interaction.followup.send(
                    "⚠️ Nenhum servidor CS:GO Legacy cadastrado neste Discord. Configure pelo `/painel`."
                )
                return

            embed = EmbedFactory.build(
                cfg,
                title=f"🔫 Servidores CS:GO Legacy • {cfg.brand_name if cfg else 'FUSION'}",
                description="Status consultado diretamente nos servidores via protocolo **Valve A2S**:",
            )

            for srv in servers[:10]:
                status = await CsgoService.query_single_server(srv, timeout=2.5)
                if status["is_maintenance"]:
                    badge = "🛠️ EM MANUTENÇÃO"
                elif status["is_online"]:
                    badge = "🟢 ONLINE"
                else:
                    badge = "🔴 OFFLINE"

                map_name = status["map"] or "N/A"
                players = f"{status['players']}/{status['max_players']}"
                embed.add_field(
                    name=f"{badge} • {srv.name} [{srv.server_type}]",
                    value=(
                        f"• **Mapa:** `{map_name}` | **Slots:** `{players}`\n"
                        f"• **Conectar:** `connect {srv.ip_address}:{srv.port}`"
                    ),
                    inline=False,
                )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="csgo-ranking",
        description="Exibe o ranking competitivo de jogadores MIX da FUSION.",
    )
    @app_commands.guild_only()
    async def csgo_ranking(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            players = await CsgoService.get_top_players(session, interaction.guild.id, limit=10)

            if not players:
                await interaction.response.send_message(
                    "📊 Nenhuma partida competitiva registrada ainda pelos servidores.",
                    ephemeral=True,
                )
                return

            lines: list[str] = []
            for idx, p in enumerate(players, start=1):
                kd = round(p.kills / max(1, p.deaths), 2)
                hs_pct = round((p.headshots / max(1, p.kills)) * 100, 1)
                discord_tag = f" (<@{p.discord_user_id}>)" if p.discord_user_id else ""
                lines.append(
                    f"**#{idx}** **{p.nickname}**{discord_tag} — **{p.elo_points} ELO** | "
                    f"K/D: `{kd}` | HS: `{hs_pct}%` | Vitórias: `{p.wins}/{p.matches_played}`"
                )

            embed = EmbedFactory.build(
                cfg,
                title="🏆 Ranking Competitivo CS:GO Legacy • FUSION",
                description="\n".join(lines),
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="csgo-partidas",
        description="Lista as últimas partidas MIX/Retake registradas pelos servidores.",
    )
    @app_commands.guild_only()
    async def recent_matches(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            matches = (
                await session.execute(
                    select(CsgoMatchHistory)
                    .where(CsgoMatchHistory.guild_id == interaction.guild.id)
                    .order_by(desc(CsgoMatchHistory.played_at))
                    .limit(10)
                )
            ).scalars().all()

            if not matches:
                await interaction.response.send_message(
                    "📋 Nenhuma partida registrada até o momento.",
                    ephemeral=True,
                )
                return

            lines = [
                f"• `{m.match_uid}` | Mapa: **`{m.map_name}`** | Placar: **CT {m.score_ct} x {m.score_t} T** "
                f"(<t:{int(m.played_at.timestamp())}:R>)"
                for m in matches
            ]
            embed = EmbedFactory.build(
                cfg,
                title="🎮 Histórico Recente de Partidas • FUSION",
                description="\n".join(lines),
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CsgoCog(bot))