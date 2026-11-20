import re
from typing import Optional

import discord
from sqlalchemy import select

from core.database import get_db_session
from core.permissions import rate_limiter
from database.models.guild import GuildConfig
from database.models.tickets import Ticket, TicketDepartment, TicketPanel, TicketStatus
from modules.tickets.modals import (
    TicketCloseReasonModal,
    TicketOpenSubjectModal,
    TicketRatingModal,
    TicketRenameModal,
)
from modules.tickets.service import TicketService
from modules.tickets.transcript import TranscriptGenerator


def _parse_button_style(style_name: str) -> discord.ButtonStyle:
    mapping = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }
    return mapping.get((style_name or "").lower(), discord.ButtonStyle.secondary)


def _safe_emoji(raw: Optional[str]) -> Optional[discord.PartialEmoji | str]:
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return discord.PartialEmoji.from_str(cleaned)
    except Exception:
        return cleaned


# ==============================================================================
# DYNAMIC ITEMS PERSISTENTES (SOBREVIVEM A REINICIALIZAÇÕES DO BOT)
# ==============================================================================
class TicketOpenButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:ticket:open:(?P<panel_id>\d+):(?P<dept_id>\d+)",
):
    def __init__(
        self,
        panel_id: int,
        dept_id: int,
        label: str = "Abrir Ticket",
        emoji: Optional[discord.PartialEmoji | str] = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji=emoji,
                style=style,
                custom_id=f"fusion:ticket:open:{panel_id}:{dept_id}",
            )
        )
        self.panel_id = panel_id
        self.dept_id = dept_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "TicketOpenButton":
        return cls(
            panel_id=int(match["panel_id"]),
            dept_id=int(match["dept_id"]),
            label=item.label or "Abrir Ticket",
            emoji=item.emoji,
            style=item.style,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        allowed, retry = rate_limiter.check(interaction.user.id, "open_ticket", max_calls=2, window_seconds=15.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Aguarde `{retry}s` antes de abrir outro ticket.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketOpenSubjectModal(self.panel_id, self.dept_id))


class TicketOpenSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"fusion:ticket:select:(?P<panel_id>\d+)",
):
    def __init__(
        self,
        panel_id: int,
        placeholder: str = "Selecione o departamento...",
        options: Optional[list[discord.SelectOption]] = None,
    ) -> None:
        super().__init__(
            discord.ui.Select(
                placeholder=placeholder[:150],
                min_values=1,
                max_values=1,
                options=options or [discord.SelectOption(label="Carregando...", value="0")],
                custom_id=f"fusion:ticket:select:{panel_id}",
            )
        )
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
    ) -> "TicketOpenSelect":
        return cls(
            panel_id=int(match["panel_id"]),
            placeholder=item.placeholder or "Selecione o departamento...",
            options=list(item.options),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        allowed, retry = rate_limiter.check(interaction.user.id, "open_ticket", max_calls=2, window_seconds=15.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Aguarde `{retry}s` antes de abrir outro ticket.",
                ephemeral=True,
            )
            return

        selected = self.item.values[0]
        if not selected.isdigit() or int(selected) == 0:
            await interaction.response.send_message("❌ Departamento inválido.", ephemeral=True)
            return

        await interaction.response.send_modal(TicketOpenSubjectModal(self.panel_id, int(selected)))


class TicketActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:ticket:act:(?P<action>[a-z_]+)",
):
    def __init__(
        self,
        action: str,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        row: int = 0,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"fusion:ticket:act:{action}",
                row=row,
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "TicketActionButton":
        return cls(
            action=match["action"],
            label=item.label or match["action"],
            emoji=str(item.emoji) if item.emoji else "🎫",
            style=item.style,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)

        async with get_db_session() as session:
            guild_cfg = await session.get(GuildConfig, interaction.guild.id)
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()

            if not ticket:
                await interaction.response.send_message("❌ Este canal não está vinculado a um ticket.", ephemeral=True)
                return

            dept = (
                await session.get(TicketDepartment, ticket.department_id)
                if ticket.department_id
                else None
            )
            is_staff = TicketService.is_staff_member(interaction.user, guild_cfg, dept)

            # Ações restritas à equipe staff (exceto fechar/transcript que o autor também pode acionar)
            staff_only_actions = {"claim", "members", "rename", "transfer", "change_dept", "reopen", "delete"}
            if self.action in staff_only_actions and not is_staff:
                await interaction.response.send_message(
                    "🚫 Apenas membros da equipe de atendimento podem executar esta ação.",
                    ephemeral=True,
                )
                return

            if self.action == "claim":
                msg = await TicketService.claim_ticket(
                    interaction.client, session, interaction.guild, ticket, interaction.user
                )
                await interaction.response.send_message(msg)

            elif self.action == "close":
                await interaction.response.send_modal(TicketCloseReasonModal())

            elif self.action == "rename":
                await interaction.response.send_modal(TicketRenameModal())

            elif self.action == "members":
                await interaction.response.send_message(
                    "👥 Selecione um membro para **adicionar** ou **remover** deste ticket:",
                    view=TicketMemberManageView(),
                    ephemeral=True,
                )

            elif self.action == "transfer":
                await interaction.response.send_message(
                    "🔄 Selecione o atendente que assumirá a responsabilidade deste ticket:",
                    view=TicketTransferView(),
                    ephemeral=True,
                )

            elif self.action == "change_dept":
                depts = (
                    await session.execute(
                        select(TicketDepartment).where(TicketDepartment.guild_id == interaction.guild.id)
                    )
                ).scalars().all()
                if not depts:
                    await interaction.response.send_message("⚠️ Nenhum outro departamento configurado.", ephemeral=True)
                    return
                await interaction.response.send_message(
                    "🗂️ Selecione o novo departamento para este ticket:",
                    view=TicketChangeDepartmentView(list(depts)),
                    ephemeral=True,
                )

            elif self.action == "transcript":
                await interaction.response.defer(ephemeral=True)
                path = await TranscriptGenerator.generate(session, ticket, interaction.guild)
                await interaction.followup.send(
                    content=f"📄 Transcript HTML do Ticket **#{ticket.ticket_number:04d}** gerado:",
                    file=discord.File(path, filename=path.name),
                    ephemeral=True,
                )

            elif self.action == "reopen":
                msg = await TicketService.reopen_ticket(
                    interaction.client, session, interaction.guild, interaction.channel, ticket, interaction.user
                )
                await interaction.response.send_message(msg, ephemeral=True)

            elif self.action == "delete":
                await interaction.response.send_message(
                    "🗑️ Gerando transcript final e excluindo o canal em **4 segundos**...",
                )
                await TicketService.delete_ticket(
                    interaction.client, session, interaction.guild, interaction.channel, ticket, interaction.user
                )


class TicketRateButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:ticket:rate:(?P<ticket_id>\d+):(?P<stars>[1-5])",
):
    def __init__(self, ticket_id: int, stars: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=f"{stars} ★",
                style=discord.ButtonStyle.success if stars >= 4 else discord.ButtonStyle.secondary,
                custom_id=f"fusion:ticket:rate:{ticket_id}:{stars}",
            )
        )
        self.ticket_id = ticket_id
        self.stars = stars

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "TicketRateButton":
        return cls(ticket_id=int(match["ticket_id"]), stars=int(match["stars"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketRatingModal(self.ticket_id, self.stars))


# ==============================================================================
# VIEWS PRINCIPAIS EXPOSTAS AOS OUTROS MÓDULOS
# ==============================================================================
class TicketLauncherView(discord.ui.View):
    """View pública do Painel de Tickets (suporta botões ou dropdown por departamento)."""

    def __init__(self, panel: TicketPanel, departments: list[TicketDepartment]) -> None:
        super().__init__(timeout=None)
        if panel.component_mode == "buttons":
            for dept in departments[:25]:
                self.add_item(
                    TicketOpenButton(
                        panel_id=panel.id,
                        dept_id=dept.id,
                        label=dept.name,
                        emoji=_safe_emoji(dept.emoji),
                        style=_parse_button_style(dept.button_style),
                    )
                )
        else:
            options = [
                discord.SelectOption(
                    label=dept.name[:100],
                    value=str(dept.id),
                    description=(dept.description or f"Abrir ticket em {dept.name}")[:100],
                    emoji=_safe_emoji(dept.emoji),
                )
                for dept in departments[:25]
            ]
            self.add_item(
                TicketOpenSelect(
                    panel_id=panel.id,
                    placeholder=panel.dropdown_placeholder,
                    options=options,
                )
            )


class TicketControlView(discord.ui.View):
    """Controles internos exibidos dentro de todo ticket aberto."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketActionButton("claim", "Assumir", "🙋", discord.ButtonStyle.success, row=0))
        self.add_item(TicketActionButton("close", "Fechar", "🔒", discord.ButtonStyle.danger, row=0))
        self.add_item(TicketActionButton("members", "Membros", "👥", discord.ButtonStyle.secondary, row=0))
        self.add_item(TicketActionButton("rename", "Renomear", "✏️", discord.ButtonStyle.secondary, row=0))
        self.add_item(TicketActionButton("transfer", "Transferir", "🔄", discord.ButtonStyle.primary, row=1))
        self.add_item(TicketActionButton("change_dept", "Departamento", "🗂️", discord.ButtonStyle.primary, row=1))
        self.add_item(TicketActionButton("transcript", "Transcript HTML", "📄", discord.ButtonStyle.secondary, row=1))


class TicketClosedControlView(discord.ui.View):
    """Controles exibidos quando um ticket é fechado."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketActionButton("reopen", "Reabrir", "🔓", discord.ButtonStyle.success, row=0))
        self.add_item(TicketActionButton("transcript", "Transcript HTML", "📄", discord.ButtonStyle.primary, row=0))
        self.add_item(TicketActionButton("delete", "Excluir", "🗑️", discord.ButtonStyle.danger, row=0))


class TicketRatingView(discord.ui.View):
    """Botões de avaliação 1 a 5 estrelas enviados ao cliente após o fechamento."""

    def __init__(self, ticket_id: int) -> None:
        super().__init__(timeout=None)
        for stars in range(1, 6):
            self.add_item(TicketRateButton(ticket_id, stars))


# ==============================================================================
# SUB-VIEWS EFÊMERAS DE GERENCIAMENTO INTERNO
# ==============================================================================
class TicketMemberManageView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Adicionar membro ao ticket...", min_values=1, max_values=1, row=0)
    async def add_member_select(self, interaction: discord.Interaction, select_ui: discord.ui.UserSelect) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)
        target = select_ui.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("❌ Membro não encontrado no servidor.", ephemeral=True)
            return

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            if not ticket:
                return
            msg = await TicketService.add_user(
                interaction.client, session, interaction.guild, interaction.channel, ticket, target, interaction.user
            )
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Remover membro do ticket...", min_values=1, max_values=1, row=1)
    async def remove_member_select(self, interaction: discord.Interaction, select_ui: discord.ui.UserSelect) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)
        target = select_ui.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("❌ Membro não encontrado.", ephemeral=True)
            return

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            if not ticket:
                return
            msg = await TicketService.remove_user(
                interaction.client, session, interaction.guild, interaction.channel, ticket, target, interaction.user
            )
        await interaction.response.edit_message(content=msg, view=None)


class TicketTransferView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Selecione o novo atendente responsável...", min_values=1, max_values=1)
    async def transfer_select(self, interaction: discord.Interaction, select_ui: discord.ui.UserSelect) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)
        target = select_ui.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message("❌ Membro inválido.", ephemeral=True)
            return

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            if not ticket:
                return
            msg = await TicketService.transfer_ticket(
                interaction.client, session, interaction.guild, interaction.channel, ticket, target, interaction.user
            )
        await interaction.response.edit_message(content=msg, view=None)


class DepartmentSwitchSelect(discord.ui.Select):
    def __init__(self, departments: list[TicketDepartment]) -> None:
        options = [
            discord.SelectOption(
                label=d.name[:100],
                value=str(d.id),
                description=(d.description or f"Transferir para {d.name}")[:100],
                emoji=_safe_emoji(d.emoji),
            )
            for d in departments[:25]
        ]
        super().__init__(placeholder="Escolha o departamento de destino...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        assert isinstance(interaction.channel, discord.TextChannel)
        dept_id = int(self.values[0])

        async with get_db_session() as session:
            ticket = (
                await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel.id))
            ).scalar_one_or_none()
            new_dept = await session.get(TicketDepartment, dept_id)
            if not ticket or not new_dept:
                return
            msg = await TicketService.change_department(
                interaction.client, session, interaction.guild, interaction.channel, ticket, new_dept, interaction.user
            )
        await interaction.response.edit_message(content=msg, view=None)


class TicketChangeDepartmentView(discord.ui.View):
    def __init__(self, departments: list[TicketDepartment]) -> None:
        super().__init__(timeout=60)
        self.add_item(DepartmentSwitchSelect(departments))