from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from database.models.guild import GuildConfig
from database.models.tickets import (
    Ticket,
    TicketDepartment,
    TicketMessage,
    TicketPanel,
    TicketRating,
    TicketStatus,
)
from database.models.vip import UserVip, VipPlan, VipStatus
from modules.tickets.transcript import TranscriptGenerator
from modules.vip.service import VipService


@pytest.mark.asyncio
async def test_ticket_html_transcript_escapes_xss_and_includes_rating(
    db_session,
    mock_discord_env,
) -> None:
    _, guild, member = mock_discord_env
    db_session.add(GuildConfig(guild_id=guild.id, guild_name=guild.name))

    panel = TicketPanel(
        guild_id=guild.id,
        name="Suporte Geral",
        title="Central FUSION",
        description="Abra seu ticket",
    )
    db_session.add(panel)
    await db_session.flush()

    dept = TicketDepartment(
        guild_id=guild.id,
        panel_id=panel.id,
        name="Suporte Técnico",
    )
    db_session.add(dept)
    await db_session.flush()

    ticket = Ticket(
        guild_id=guild.id,
        ticket_number=42,
        panel_id=panel.id,
        department_id=dept.id,
        channel_id=777888999111222333,
        opener_id=member.id,
        claimed_by_id=111222333444555666,
        status=TicketStatus.CLOSED,
        subject="Erro de conexão MIX",
        close_reason="Resolvido",
    )
    db_session.add(ticket)
    await db_session.flush()

    db_session.add(
        TicketMessage(
            ticket_id=ticket.id,
            discord_message_id=900001,
            author_id=member.id,
            author_name="FUSION USER",
            is_staff=False,
            content="<script>alert('xss')</script> Olá equipe!",
        )
    )
    db_session.add(
        TicketRating(
            guild_id=guild.id,
            ticket_id=ticket.id,
            user_id=member.id,
            staff_id=111222333444555666,
            stars=5,
            comment="Atendimento rápido!",
        )
    )
    await db_session.flush()

    html_path = await TranscriptGenerator.generate(db_session, ticket, guild)
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")

    assert "<script>" not in content
    assert "<script>alert('xss')</script>" in content
    assert "Ticket #0042" in content
    assert "Atendimento rápido!" in content


@pytest.mark.asyncio
async def test_vip_expiration_and_preventive_notification(
    db_session,
    mock_discord_env,
) -> None:
    bot, guild, member = mock_discord_env
    now = datetime.now(timezone.utc)

    plan = VipPlan(
        guild_id=guild.id,
        name="VIP Prata",
        description="VIP Prata CS:GO",
        price=Decimal("20.00"),
        duration_days=30,
        role_id=555666777888999000,
        notify_days_before=[7, 3, 1],
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()

    # VIP já vencido -> deve expirar e remover cargo
    expired_vip = UserVip(
        guild_id=guild.id,
        user_id=member.id,
        plan_id=plan.id,
        started_at=now - timedelta(days=31),
        expires_at=now - timedelta(minutes=10),
        status=VipStatus.ACTIVE,
    )
    # VIP vencendo em 2 dias -> deve disparar aviso de 3 dias
    expiring_soon_vip = UserVip(
        guild_id=guild.id,
        user_id=member.id,
        plan_id=plan.id,
        started_at=now - timedelta(days=28),
        expires_at=now + timedelta(days=2),
        status=VipStatus.ACTIVE,
    )
    db_session.add_all([expired_vip, expiring_soon_vip])
    await db_session.flush()

    expired_count, notified_count = await VipService.process_expirations_and_notifications(bot, db_session)
    assert expired_count == 1
    assert notified_count == 1
    assert expired_vip.status == VipStatus.EXPIRED
    assert 3 in expiring_soon_vip.notified_thresholds