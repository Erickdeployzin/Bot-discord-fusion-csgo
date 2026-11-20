from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import desc, func, select

from bot.embeds.factory import EmbedFactory
from core.database import get_db_session
from core.permissions import has_module_permission
from database.models.guild import BotModule, GuildConfig, LogCategory
from database.models.tickets import (
    Ticket,
    TicketDepartment,
    TicketMessage,
    TicketRating,
    TicketStatus,
)
from modules.logs.service import LogService
from modules.tickets.service import TicketService
from modules.tickets.transcript import TranscriptGenerator
from modules.tickets.views import (
    TicketActionButton,
    TicketOpenButton,
    TicketOpenSelect,
    TicketRateButton,
)


class DepartmentConfiguratorView(discord.ui.View):
    """Permite configurar cargos de atendimento, categoria e canal de logs do departamento sem editar arquivos."""

    def __init__(self, owner_id: int, dept_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.dept_id = dept_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("🔒 Apenas quem abriu este menu pode editar.", ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.RoleSelect, 
        placeholder="Selecione os Cargos de Atendimento deste departamento...",
        min_values=1,
        max_values=10,
        row=0,
    )
    async def roles_select(self, interaction: discord.Interaction, select_ui: discord.ui.RoleSelect) -> None:
        assert interaction.guild is not None
        role_ids = [r.id for r in select_ui.values]
        async with get_db_session() as session:
            dept = await session.get(TicketDepartment, self.dept_id)
            if dept and dept.guild_id == interaction.guild.id:
                dept.support_role_ids = role_ids
                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.TICKETS,
                    title="Cargos do Departamento Atualizados",
                    description=f"Departamento **{dept.name}** vinculado a {len(role_ids)} cargo(s).",
                    actor=interaction.user,
                )
        mentions = ", ".join(f"<@&{rid}>" for rid in role_ids)
        await interaction.response.send_message(f"✅ Cargos de atendimento atualizados: {mentions}", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, 
        placeholder="Selecione a Categoria onde os canais serão criados...",
        channel_types=[discord.ChannelType.category],
        min_values=1,
        max_values=1,
        row=1,
    )
    async def category_select(self, interaction: discord.Interaction, select_ui: discord.ui.ChannelSelect) -> None:
        assert interaction.guild is not None
        cat = select_ui.values[0]
        async with get_db_session() as session:
            dept = await session.get(TicketDepartment, self.dept_id)
            if dept and dept.guild_id == interaction.guild.id:
                dept.category_id = cat.id
        await interaction.response.send_message(f"✅ Categoria definida para **{cat.name}**.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, 
        placeholder="Selecione o Canal de Logs exclusivo deste departamento...",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
        row=2,
    )
    async def log_channel_select(self, interaction: discord.Interaction, select_ui: discord.ui.ChannelSelect) -> None:
        assert interaction.guild is not None
        ch = select_ui.values[0]
        async with get_db_session() as session:
            dept = await session.get(TicketDepartment, self.dept_id)
            if dept and dept.guild_id == interaction.guild.id:
                dept.log_channel_id = ch.id
        await interaction.response.send_message(f"✅ Canal de logs do departamento definido para <#{ch.id}>.", ephemeral=True)


class TicketsCog(commands.Cog):
    """Cog responsável por persistência de botões, gravação de mensagens e comandos de tickets."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(
            TicketOpenButton,
            TicketOpenSelect,
            TicketActionButton,
            TicketRateButton,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        async with get_db_session() as session:
            ticket = (
                await session.execute(
                    select(Ticket).where(
                        Ticket.channel_id == message.channel.id,
                        Ticket.status != TicketStatus.DELETED,
                    )
                )
            ).scalar_one_or_none()

            if not ticket:
                return

            guild_cfg = await session.get(GuildConfig, message.guild.id)
            dept = (
                await session.get(TicketDepartment, ticket.department_id)
                if ticket.department_id
                else None
            )
            is_staff = (
                TicketService.is_staff_member(message.author, guild_cfg, dept)
                if isinstance(message.author, discord.Member)
                else False
            )

            attachments = [att.url for att in message.attachments]
            session.add(
                TicketMessage(
                    ticket_id=ticket.id,
                    discord_message_id=message.id,
                    author_id=message.author.id,
                    author_name=message.author.display_name,
                    author_avatar_url=message.author.display_avatar.url if message.author.display_avatar else None,
                    is_staff=is_staff,
                    content=message.content or "",
                    attachments_json=attachments,
                )
            )

    @app_commands.command(
        name="ticket-config-depto",
        description="Configura cargos de atendimento, categoria e canal de logs de um departamento.",
    )
    @app_commands.describe(departamento_id="ID numérico do departamento exibido no /painel")
    @app_commands.guild_only()
    async def configure_department(self, interaction: discord.Interaction, departamento_id: int) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.TICKETS):
                await interaction.response.send_message("🚫 Sem permissão para gerenciar tickets.", ephemeral=True)
                return

            cfg = await session.get(GuildConfig, interaction.guild.id)
            dept = await session.get(TicketDepartment, departamento_id)
            if not dept or dept.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Departamento não encontrado neste servidor.", ephemeral=True)
                return

            roles_txt = (
                ", ".join(f"<@&{rid}>" for rid in (dept.support_role_ids or []))
                or "`Nenhum cargo vinculado`"
            )
            embed = EmbedFactory.build(
                cfg,
                title=f"🗂️ Configurar Departamento • {dept.name} (#{dept.id})",
                description=(
                    f"• **Cargos de Atendimento:** {roles_txt}\n"
                    f"• **Categoria:** {f'<#{dept.category_id}>' if dept.category_id else '`Não definida`'}\n"
                    f"• **Canal de Logs:** {f'<#{dept.log_channel_id}>' if dept.log_channel_id else '`Não definido`'}"
                ),
            )

        await interaction.response.send_message(
            embed=embed,
            view=DepartmentConfiguratorView(interaction.user.id, departamento_id),
            ephemeral=True,
        )

    @app_commands.command(
        name="ticket-ranking",
        description="Exibe o ranking de desempenho e avaliações dos atendentes da FUSION.",
    )
    @app_commands.guild_only()
    async def staff_ranking(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.TICKETS):
                await interaction.response.send_message("🚫 Sem permissão para visualizar estatísticas.", ephemeral=True)
                return

            cfg = await session.get(GuildConfig, interaction.guild.id)

            tickets_stmt = (
                select(
                    Ticket.claimed_by_id,
                    func.count(Ticket.id).label("total_tickets"),
                )
                .where(
                    Ticket.guild_id == interaction.guild.id,
                    Ticket.claimed_by_id.is_not(None),
                )
                .group_by(Ticket.claimed_by_id)
                .order_by(desc("total_tickets"))
                .limit(10)
            )
            staff_rows = (await session.execute(tickets_stmt)).all()

            ratings_stmt = (
                select(
                    TicketRating.staff_id,
                    func.avg(TicketRating.stars).label("avg_stars"),
                    func.count(TicketRating.id).label("rating_count"),
                )
                .where(
                    TicketRating.guild_id == interaction.guild.id,
                    TicketRating.staff_id.is_not(None),
                )
                .group_by(TicketRating.staff_id)
            )
            rating_map = {
                row.staff_id: (round(float(row.avg_stars), 2), int(row.rating_count))
                for row in (await session.execute(ratings_stmt)).all()
            }

            if not staff_rows:
                await interaction.response.send_message(
                    "📊 Ainda não há atendimentos assumidos registrados neste servidor.",
                    ephemeral=True,
                )
                return

            lines: list[str] = []
            for idx, (staff_id, total_tickets) in enumerate(staff_rows, start=1):
                avg_stars, r_count = rating_map.get(staff_id, (0.0, 0))
                star_txt = f"⭐ {avg_stars}/5 ({r_count} aval.)" if r_count > 0 else "Sem avaliações"
                lines.append(f"**#{idx}** <@{staff_id}> — **{total_tickets}** atendimentos | {star_txt}")

            embed = EmbedFactory.build(
                cfg,
                title="🏆 Ranking de Atendimento • Equipe FUSION",
                description="\n".join(lines),
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="ticket-historico",
        description="Consulta detalhes e baixa o transcript HTML de qualquer ticket pelo número.",
    )
    @app_commands.describe(numero="Número do ticket (ex: 12)")
    @app_commands.guild_only()
    async def ticket_history(self, interaction: discord.Interaction, numero: int) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.TICKETS):
                await interaction.response.send_message("🚫 Sem permissão.", ephemeral=True)
                return

            cfg = await session.get(GuildConfig, interaction.guild.id)
            ticket = (
                await session.execute(
                    select(Ticket).where(
                        Ticket.guild_id == interaction.guild.id,
                        Ticket.ticket_number == numero,
                    )
                )
            ).scalar_one_or_none()

            if not ticket:
                await interaction.response.send_message(f"❌ Ticket `#{numero:04d}` não encontrado.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            path = await TranscriptGenerator.generate(session, ticket, interaction.guild)

            embed = EmbedFactory.build(
                cfg,
                title=f"📋 Histórico • Ticket #{ticket.ticket_number:04d}",
                description=f"Status atual: **{ticket.status.value.upper()}**",
            )
            embed.add_field(name="Cliente", value=f"<@{ticket.opener_id}>", inline=True)
            embed.add_field(
                name="Responsável",
                value=f"<@{ticket.claimed_by_id}>" if ticket.claimed_by_id else "`Nenhum`",
                inline=True,
            )
            embed.add_field(name="Assunto", value=ticket.subject or "Não informado", inline=False)

        await interaction.followup.send(
            embed=embed,
            file=discord.File(path, filename=path.name),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))