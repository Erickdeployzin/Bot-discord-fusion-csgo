from typing import Optional

import discord
from sqlalchemy import select

from core.database import get_db_session
from database.models.guild import GuildConfig, LogCategory
from database.models.tickets import Ticket, TicketDepartment, TicketPanel, TicketRating
from modules.logs.service import LogService
from modules.tickets.service import TicketService


class TicketOpenSubjectModal(discord.ui.Modal, title="Abrir Atendimento - FUSION"):
    subject = discord.ui.TextInput(
        label="Assunto do Atendimento",
        placeholder="Ex: Dúvida sobre VIP Diamante / Problema no MIX #1",
        max_length=150,
        required=True,
    )
    details = discord.ui.TextInput(
        label="Detalhes Adicionais ou SteamID (Opcional)",
        style=discord.TextStyle.paragraph,
        placeholder="Informe seu SteamID ou descreva o ocorrido para agilizar o suporte...",
        max_length=1000,
        required=False,
    )

    def __init__(self, panel_id: int, department_id: int) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.department_id = department_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        await interaction.response.defer(ephemeral=True)

        full_subject = self.subject.value.strip()
        if self.details.value.strip():
            full_subject = f"{full_subject} — {self.details.value.strip()}"[:200]

        async with get_db_session() as session:
            panel = await session.get(TicketPanel, self.panel_id)
            dept = await session.get(TicketDepartment, self.department_id)
            if not panel or not dept or panel.guild_id != interaction.guild.id:
                await interaction.followup.send("❌ Painel ou departamento inválido.", ephemeral=True)
                return

            ticket, channel, error = await TicketService.open_ticket(
                interaction.client,
                session,
                interaction.guild,
                interaction.user,
                panel,
                dept,
                subject=full_subject,
            )

        if error or not channel or not ticket:
            await interaction.followup.send(error or "❌ Não foi possível abrir o ticket.", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Ticket **#{ticket.ticket_number:04d}** criado com sucesso! Acesse: {channel.mention}",
            ephemeral=True,
        )


class TicketCloseReasonModal(discord.ui.Modal, title="Fechar Ticket"):
    reason = discord.ui.TextInput(
        label="Motivo do Fechamento",
        placeholder="Ex: Solicitação resolvida com sucesso.",
        default="Atendimento concluído.",
        max_length=300,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer(ephemeral=True)

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            if not ticket:
                await interaction.followup.send("❌ Este canal não é um ticket válido.", ephemeral=True)
                return

            await TicketService.close_ticket(
                interaction.client,
                session,
                interaction.guild,
                interaction.channel,
                ticket,
                interaction.user,
                reason=self.reason.value,
            )

        await interaction.followup.send("🔒 Ticket fechado e transcript gerado.", ephemeral=True)


class TicketRenameModal(discord.ui.Modal, title="Renomear Canal do Ticket"):
    new_name = discord.ui.TextInput(
        label="Novo Nome / Assunto",
        placeholder="Ex: vip-diamante-erick",
        max_length=60,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer(ephemeral=True)

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            if not ticket:
                await interaction.followup.send("❌ Ticket não encontrado.", ephemeral=True)
                return

            msg = await TicketService.rename_ticket(
                interaction.client,
                session,
                interaction.guild,
                interaction.channel,
                ticket,
                self.new_name.value,
                interaction.user,
            )

        await interaction.followup.send(msg, ephemeral=True)


class TicketRatingModal(discord.ui.Modal, title="Avaliação de Atendimento - FUSION"):
    comment = discord.ui.TextInput(
        label="Comentário sobre o atendimento (Opcional)",
        style=discord.TextStyle.paragraph,
        placeholder="Conte como foi sua experiência com nossa equipe...",
        max_length=500,
        required=False,
    )

    def __init__(self, ticket_id: int, stars: int) -> None:
        super().__init__()
        self.ticket_id = ticket_id
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction) -> None:
        async with get_db_session() as session:
            ticket = await session.get(Ticket, self.ticket_id)
            if not ticket:
                await interaction.response.send_message("❌ Ticket não encontrado.", ephemeral=True)
                return

            if ticket.opener_id != interaction.user.id:
                await interaction.response.send_message(
                    "🚫 Apenas o autor do ticket pode avaliá-lo.",
                    ephemeral=True,
                )
                return

            existing = (
                await session.execute(select(TicketRating).where(TicketRating.ticket_id == ticket.id))
            ).scalar_one_or_none()

            if existing:
                existing.stars = self.stars
                existing.comment = self.comment.value.strip() or None
            else:
                session.add(
                    TicketRating(
                        guild_id=ticket.guild_id,
                        ticket_id=ticket.id,
                        user_id=interaction.user.id,
                        staff_id=ticket.claimed_by_id,
                        stars=self.stars,
                        comment=self.comment.value.strip() or None,
                    )
                )

            await LogService.dispatch(
                interaction.client,
                session,
                ticket.guild_id,
                LogCategory.SUPPORT,
                title=f"Avaliação Recebida • Ticket #{ticket.ticket_number:04d}",
                description=f"Nota: {'⭐' * self.stars} (**{self.stars}/5**)",
                actor=interaction.user,
                fields=[
                    ("Atendente", f"<@{ticket.claimed_by_id}>" if ticket.claimed_by_id else "`Não atribuído`", True),
                    ("Comentário", self.comment.value.strip() or "Sem comentário", False),
                ],
            )

        await interaction.response.send_message(
            f"✅ Obrigado pela sua avaliação de **{'⭐' * self.stars} ({self.stars}/5)**!",
            ephemeral=True,
        )