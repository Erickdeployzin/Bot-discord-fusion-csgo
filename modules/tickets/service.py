import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.embeds.factory import EmbedFactory
from core.logger import logger
from database.models.guild import GuildConfig, LogCategory
from database.models.tickets import (
    Ticket,
    TicketDepartment,
    TicketMessage,
    TicketPanel,
    TicketRating,
    TicketStatus,
)
from modules.logs.service import LogService
from modules.tickets.transcript import TranscriptGenerator
from utils.helpers import parse_hex_color, render_template_variables


def _slugify_channel(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\-_]", "-", text.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:40] or "ticket"


class TicketService:
    """Regras de negócio completas do sistema de tickets da FUSION."""

    @staticmethod
    def is_staff_member(
        member: discord.Member,
        guild_cfg: Optional[GuildConfig],
        dept: Optional[TicketDepartment],
    ) -> bool:
        if member.guild_permissions.administrator or member.guild.owner_id == member.id:
            return True
        member_roles = {r.id for r in member.roles}
        if guild_cfg and guild_cfg.staff_role_id and guild_cfg.staff_role_id in member_roles:
            return True
        if dept and dept.support_role_ids:
            if member_roles.intersection(set(dept.support_role_ids)):
                return True
        return False

    @staticmethod
    async def send_department_log(
        guild: discord.Guild,
        dept: Optional[TicketDepartment],
        embed: discord.Embed,
        file: Optional[discord.File] = None,
    ) -> None:
        if not dept or not dept.log_channel_id:
            return
        ch = guild.get_channel(dept.log_channel_id)
        if isinstance(ch, discord.TextChannel):
            try:
                if file:
                    await ch.send(embed=embed, file=file)
                else:
                    await ch.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"[Guild {guild.id}] Sem permissão no canal de log do departamento {dept.id}")

    @staticmethod
    async def open_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        member: discord.Member,
        panel: TicketPanel,
        dept: TicketDepartment,
        subject: Optional[str] = None,
    ) -> tuple[Optional[Ticket], Optional[discord.TextChannel], Optional[str]]:
        guild_cfg = await session.get(GuildConfig, guild.id)
        if guild_cfg and guild_cfg.maintenance_mode:
            return None, None, "🚧 O sistema está em **modo manutenção** no momento."

        if not panel.is_active:
            return None, None, "⚠️ Este painel de tickets está temporariamente desativado."

        active_count = (
            await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.guild_id == guild.id,
                    Ticket.opener_id == member.id,
                    Ticket.status.in_([TicketStatus.OPEN, TicketStatus.CLAIMED]),
                )
            )
        ).scalar_one()

        if active_count >= panel.max_tickets_per_user:
            return (
                None,
                None,
                f"⚠️ Você atingiu o limite máximo de **{panel.max_tickets_per_user}** ticket(s) aberto(s) simultaneamente.",
            )

        max_num = (
            await session.execute(
                select(func.coalesce(func.max(Ticket.ticket_number), 0)).where(Ticket.guild_id == guild.id)
            )
        ).scalar_one()
        next_number = int(max_num) + 1

        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
            )

        role_ids_to_grant = set(dept.support_role_ids or [])
        if guild_cfg and guild_cfg.staff_role_id:
            role_ids_to_grant.add(guild_cfg.staff_role_id)

        for rid in role_ids_to_grant:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    manage_messages=True,
                )

        category = guild.get_channel(dept.category_id) if dept.category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        channel_name = f"ticket-{next_number:04d}-{_slugify_channel(member.name)}"
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{next_number:04d} | Departamento: {dept.name} | Cliente: {member} ({member.id})",
                reason=f"Abertura de ticket #{next_number:04d} por {member}",
            )
        except discord.Forbidden:
            return None, None, "❌ O bot não possui permissão `Gerenciar Canais` para criar o canal do ticket."

        ticket = Ticket(
            guild_id=guild.id,
            ticket_number=next_number,
            panel_id=panel.id,
            department_id=dept.id,
            channel_id=channel.id,
            opener_id=member.id,
            status=TicketStatus.OPEN,
            subject=subject.strip() if subject else None,
        )
        session.add(ticket)
        await session.flush()

        tz_name = guild_cfg.timezone if guild_cfg else "America/Sao_Paulo"
        welcome_title = render_template_variables(
            dept.welcome_title,
            guild=guild,
            user=member,
            tz_name=tz_name,
        )
        welcome_desc = render_template_variables(
            dept.welcome_description,
            guild=guild,
            user=member,
            tz_name=tz_name,
        )

        embed = EmbedFactory.build(
            guild_cfg,
            title=f"{dept.emoji or '🎫'} {welcome_title} • #{next_number:04d}",
            description=welcome_desc,
            color_override=parse_hex_color(dept.welcome_color_hex),
            image_url=dept.welcome_image_url,
            thumbnail_url=member.display_avatar.url,
        )
        embed.add_field(name="Departamento", value=f"`{dept.name}`", inline=True)
        embed.add_field(name="Cliente", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Responsável", value="`Aguardando atendente`", inline=True)
        if ticket.subject:
            embed.add_field(name="Assunto Informado", value=ticket.subject[:1024], inline=False)

        staff_mentions = " ".join(f"<@&{rid}>" for rid in role_ids_to_grant if guild.get_role(rid))
        mention_line = f"{member.mention} {staff_mentions}".strip()

        from modules.tickets.views import TicketControlView

        control_view = TicketControlView()
        welcome_msg = await channel.send(content=mention_line, embed=embed, view=control_view)

        session.add(
            TicketMessage(
                ticket_id=ticket.id,
                discord_message_id=welcome_msg.id,
                author_id=bot.user.id if bot.user else 0,
                author_name=guild_cfg.brand_name if guild_cfg else "FUSION BOT",
                author_avatar_url=bot.user.display_avatar.url if bot.user else None,
                is_staff=True,
                content=f"[ABERTURA] Departamento: {dept.name} | Assunto: {ticket.subject or 'N/A'}",
            )
        )

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.TICKETS,
            title=f"Ticket #{next_number:04d} Aberto",
            description=f"Canal {channel.mention} aberto no departamento **{dept.name}**.",
            actor=member,
            fields=[
                ("Ticket", f"`#{next_number:04d}`", True),
                ("Departamento", dept.name, True),
                ("Assunto", ticket.subject or "Não informado", False),
            ],
        )

        return ticket, channel, None

    @staticmethod
    async def claim_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        ticket: Ticket,
        staff: discord.Member,
    ) -> str:
        if ticket.status == TicketStatus.CLOSED:
            return "⚠️ Reabra o ticket antes de assumi-lo."

        ticket.claimed_by_id = staff.id
        ticket.status = TicketStatus.CLAIMED

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Ticket #{ticket.ticket_number:04d} Assumido",
            description=f"O atendente {staff.mention} assumiu o atendimento em <#{ticket.channel_id}>.",
            actor=staff,
        )
        return f"✅ Atendimento assumido por {staff.mention}."

    @staticmethod
    async def add_user(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        target: discord.Member,
        actor: discord.Member,
    ) -> str:
        await channel.set_permissions(
            target,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        )
        current = set(ticket.added_user_ids or [])
        current.add(target.id)
        ticket.added_user_ids = list(current)

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Usuário Adicionado ao Ticket #{ticket.ticket_number:04d}",
            description=f"{target.mention} foi adicionado ao canal {channel.mention}.",
            actor=actor,
        )
        return f"✅ {target.mention} foi adicionado ao ticket."

    @staticmethod
    async def remove_user(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        target: discord.Member,
        actor: discord.Member,
    ) -> str:
        if target.id == ticket.opener_id:
            return "❌ Não é possível remover o autor original do ticket."

        await channel.set_permissions(target, overwrite=None)
        current = [uid for uid in (ticket.added_user_ids or []) if uid != target.id]
        ticket.added_user_ids = current

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Usuário Removido do Ticket #{ticket.ticket_number:04d}",
            description=f"{target.mention} foi removido do canal {channel.mention}.",
            actor=actor,
        )
        return f"✅ {target.mention} foi removido do ticket."

    @staticmethod
    async def rename_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        new_name: str,
        actor: discord.Member,
    ) -> str:
        slug = _slugify_channel(new_name)
        formatted = f"ticket-{ticket.ticket_number:04d}-{slug}"
        await channel.edit(name=formatted, reason=f"Ticket renomeado por {actor}")
        ticket.subject = new_name.strip()[:200]

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Ticket #{ticket.ticket_number:04d} Renomeado",
            description=f"Canal renomeado para `{formatted}`.",
            actor=actor,
        )
        return f"✅ Canal renomeado para `{formatted}`."

    @staticmethod
    async def transfer_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        new_staff: discord.Member,
        actor: discord.Member,
    ) -> str:
        await channel.set_permissions(
            new_staff,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
        ticket.claimed_by_id = new_staff.id
        ticket.status = TicketStatus.CLAIMED

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Ticket #{ticket.ticket_number:04d} Transferido",
            description=f"Responsabilidade transferida de {actor.mention} para {new_staff.mention}.",
            actor=actor,
        )
        return f"🔄 Ticket transferido para {new_staff.mention}."

    @staticmethod
    async def change_department(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        new_dept: TicketDepartment,
        actor: discord.Member,
    ) -> str:
        old_dept = (
            await session.get(TicketDepartment, ticket.department_id)
            if ticket.department_id
            else None
        )

        # Remove permissões dos cargos exclusivos do departamento anterior
        if old_dept and old_dept.support_role_ids:
            new_role_set = set(new_dept.support_role_ids or [])
            for rid in old_dept.support_role_ids:
                if rid not in new_role_set:
                    role = guild.get_role(rid)
                    if role:
                        await channel.set_permissions(role, overwrite=None)

        # Concede permissões aos cargos do novo departamento
        for rid in new_dept.support_role_ids or []:
            role = guild.get_role(rid)
            if role:
                await channel.set_permissions(
                    role,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        target_category = guild.get_channel(new_dept.category_id) if new_dept.category_id else None
        if isinstance(target_category, discord.CategoryChannel):
            await channel.edit(category=target_category, reason=f"Transferência de departamento por {actor}")

        ticket.department_id = new_dept.id

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SUPPORT,
            title=f"Departamento Alterado • Ticket #{ticket.ticket_number:04d}",
            description=f"Movido para o departamento **{new_dept.name}**.",
            actor=actor,
        )
        return f"🗂️ Ticket movido para o departamento **{new_dept.name}**."

    @staticmethod
    async def close_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        actor: discord.Member,
        reason: Optional[str] = None,
    ) -> Path:
        guild_cfg = await session.get(GuildConfig, guild.id)
        dept = (
            await session.get(TicketDepartment, ticket.department_id)
            if ticket.department_id
            else None
        )

        ticket.status = TicketStatus.CLOSED
        ticket.closed_by_id = actor.id
        ticket.closed_at = datetime.now(timezone.utc)
        ticket.close_reason = reason.strip() if reason and reason.strip() else "Atendimento finalizado"

        # Bloqueia envio de mensagens do cliente e convidados, mantendo leitura
        participant_ids = {ticket.opener_id, *(ticket.added_user_ids or [])}
        for uid in participant_ids:
            member = guild.get_member(uid)
            if member:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                )

        transcript_path = await TranscriptGenerator.generate(session, ticket, guild)

        from modules.tickets.views import TicketClosedControlView, TicketRatingView

        closed_embed = EmbedFactory.warning(
            guild_cfg,
            title=f"Ticket #{ticket.ticket_number:04d} Fechado",
            description=(
                f"Fechado por: {actor.mention}\n"
                f"Motivo: **{ticket.close_reason}**\n\n"
                "Utilize os botões abaixo para reabrir, baixar o transcript HTML ou excluir o canal."
            ),
        )
        await channel.send(embed=closed_embed, view=TicketClosedControlView())

        # Envia pedido de avaliação + transcript na DM do cliente
        opener = guild.get_member(ticket.opener_id)
        if opener:
            rate_embed = EmbedFactory.build(
                guild_cfg,
                title=f"⭐ Avalie seu Atendimento • Ticket #{ticket.ticket_number:04d}",
                description=(
                    f"Olá {opener.mention}, seu atendimento em **{guild.name}** foi encerrado.\n"
                    "Clique em uma das estrelas abaixo para avaliar a qualidade do suporte!"
                ),
            )
            try:
                await opener.send(
                    embed=rate_embed,
                    view=TicketRatingView(ticket.id),
                    file=discord.File(transcript_path, filename=transcript_path.name),
                )
            except discord.Forbidden:
                # Fallback no próprio canal caso DM esteja bloqueada
                await channel.send(
                    content=opener.mention,
                    embed=rate_embed,
                    view=TicketRatingView(ticket.id),
                )

        log_fields = [
            ("Ticket", f"`#{ticket.ticket_number:04d}`", True),
            ("Cliente", f"<@{ticket.opener_id}>", True),
            ("Responsável", f"<@{ticket.claimed_by_id}>" if ticket.claimed_by_id else "`Nenhum`", True),
            ("Motivo", ticket.close_reason, False),
        ]

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.TICKETS,
            title=f"Ticket #{ticket.ticket_number:04d} Fechado",
            description=f"Canal {channel.mention} fechado por {actor.mention}.",
            actor=actor,
            fields=log_fields,
            file=discord.File(transcript_path, filename=transcript_path.name),
        )

        dept_log_embed = EmbedFactory.build(
            guild_cfg,
            title=f"🎫 Transcript • Ticket #{ticket.ticket_number:04d}",
            description=f"Atendimento finalizado por {actor.mention}.",
        )
        for fname, fval, finline in log_fields:
            dept_log_embed.add_field(name=fname, value=fval, inline=finline)
        await TicketService.send_department_log(
            guild,
            dept,
            dept_log_embed,
            file=discord.File(transcript_path, filename=transcript_path.name),
        )

        return transcript_path

    @staticmethod
    async def reopen_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        actor: discord.Member,
    ) -> str:
        guild_cfg = await session.get(GuildConfig, guild.id)
        ticket.status = TicketStatus.CLAIMED if ticket.claimed_by_id else TicketStatus.OPEN
        ticket.reopened_at = datetime.now(timezone.utc)

        participant_ids = {ticket.opener_id, *(ticket.added_user_ids or [])}
        for uid in participant_ids:
            member = guild.get_member(uid)
            if member:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

        from modules.tickets.views import TicketControlView

        reopen_embed = EmbedFactory.success(
            guild_cfg,
            title=f"Ticket #{ticket.ticket_number:04d} Reaberto",
            description=f"O atendimento foi reaberto por {actor.mention}.",
        )
        await channel.send(embed=reopen_embed, view=TicketControlView())

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.TICKETS,
            title=f"Ticket #{ticket.ticket_number:04d} Reaberto",
            description=f"Canal {channel.mention} reaberto por {actor.mention}.",
            actor=actor,
        )
        return "✅ Ticket reaberto com sucesso."

    @staticmethod
    async def delete_ticket(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: Ticket,
        actor: discord.Member,
    ) -> None:
        transcript_path = await TranscriptGenerator.generate(session, ticket, guild)
        ticket.status = TicketStatus.DELETED

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.TICKETS,
            title=f"Ticket #{ticket.ticket_number:04d} Excluído",
            description=f"Canal `{channel.name}` excluído permanentemente por {actor.mention}.",
            actor=actor,
            file=discord.File(transcript_path, filename=transcript_path.name),
        )

        async def _delayed_delete() -> None:
            await asyncio.sleep(4)
            try:
                await channel.delete(reason=f"Ticket #{ticket.ticket_number:04d} excluído por {actor}")
            except discord.HTTPException:
                pass

        asyncio.create_task(_delayed_delete())