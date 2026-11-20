import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiofiles
import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import get_settings
from database.models.guild import GuildConfig
from database.models.tickets import Ticket, TicketDepartment, TicketMessage


class TranscriptGenerator:
    """Gera transcripts HTML autocontidos com identidade visual FUSION e proteção XSS."""

    @staticmethod
    async def generate(
        session: AsyncSession,
        ticket: Ticket,
        guild: Optional[discord.Guild] = None,
    ) -> Path:
        settings = get_settings()
        stmt = (
            select(Ticket)
            .where(Ticket.id == ticket.id)
            .options(selectinload(Ticket.messages), selectinload(Ticket.rating))
        )
        full_ticket = (await session.execute(stmt)).scalar_one()

        guild_cfg = await session.get(GuildConfig, full_ticket.guild_id)
        dept = (
            await session.get(TicketDepartment, full_ticket.department_id)
            if full_ticket.department_id
            else None
        )

        brand_name = html.escape(guild_cfg.brand_name if guild_cfg else "FUSION")
        primary_color = html.escape(guild_cfg.primary_color_hex if guild_cfg else "#FF4655")
        dept_name = html.escape(dept.name if dept else "Geral")
        subject = html.escape(full_ticket.subject or "Sem assunto especificado")
        close_reason = html.escape(full_ticket.close_reason or "Não informado")
        generated_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S UTC")

        messages_html: list[str] = []
        for msg in full_ticket.messages:
            author = html.escape(msg.author_name)
            avatar = html.escape(
                msg.author_avatar_url
                or "https://cdn.discordapp.com/embed/avatars/0.png"
            )
            timestamp = msg.created_at.strftime("%d/%m/%Y %H:%M:%S")
            staff_badge = '<span class="badge-staff">STAFF</span>' if msg.is_staff else ""
            body = html.escape(msg.content).replace("\n", "<br>") if msg.content else "<em>(Sem texto)</em>"

            attachments_block = ""
            if msg.attachments_json:
                links = []
                for idx, url in enumerate(msg.attachments_json, start=1):
                    safe_url = html.escape(url)
                    links.append(f'<a href="{safe_url}" target="_blank" rel="noopener">📎 Anexo #{idx}</a>')
                attachments_block = f'<div class="attachments">{" ".join(links)}</div>'

            messages_html.append(
                f"""
                <div class="msg-row {'staff-row' if msg.is_staff else ''}">
                    <img class="avatar" src="{avatar}" alt="Avatar" />
                    <div class="msg-content">
                        <div class="msg-header">
                            <span class="author">{author}</span>
                            {staff_badge}
                            <span class="meta">ID: {msg.author_id} • {timestamp}</span>
                        </div>
                        <div class="msg-body">{body}</div>
                        {attachments_block}
                    </div>
                </div>
                """
            )

        if not messages_html:
            messages_html.append('<div class="empty">Nenhuma mensagem registrada neste ticket.</div>')

        rating_section = ""
        if full_ticket.rating:
            stars_str = "★" * full_ticket.rating.stars + "☆" * (5 - full_ticket.rating.stars)
            comment_str = html.escape(full_ticket.rating.comment or "Sem comentário adicional.")
            rating_section = f"""
            <div class="rating-box">
                <strong>Avaliação do Atendimento:</strong>
                <span class="stars">{stars_str} ({full_ticket.rating.stars}/5)</span>
                <p>{comment_str}</p>
            </div>
            """

        html_doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Transcript Ticket #{full_ticket.ticket_number:04d} - {brand_name}</title>
    <style>
        :root {{
            --primary: {primary_color};
            --bg-dark: #111214;
            --bg-card: #1e1f22;
            --bg-msg: #2b2d31;
            --text-main: #f2f3f5;
            --text-muted: #949ba4;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
            padding: 24px;
        }}
        .container {{
            max-width: 980px;
            margin: 0 auto;
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #313338;
            box-shadow: 0 10px 30px rgba(0,0,0,0.45);
        }}
        .header {{
            background: linear-gradient(135deg, #18191c 0%, #232428 100%);
            border-bottom: 3px solid var(--primary);
            padding: 24px 28px;
        }}
        .header h1 {{
            font-size: 22px;
            margin-bottom: 10px;
            color: #ffffff;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 10px;
            font-size: 13px;
            color: var(--text-muted);
        }}
        .meta-grid span strong {{ color: var(--text-main); }}
        .rating-box {{
            background: #23272a;
            border-left: 4px solid #f1c40f;
            padding: 14px 24px;
            margin: 16px 24px 0;
            border-radius: 6px;
        }}
        .stars {{ color: #f1c40f; font-size: 16px; margin-left: 8px; }}
        .messages {{ padding: 20px 24px; display: flex; flex-direction: column; gap: 14px; }}
        .msg-row {{
            display: flex;
            gap: 14px;
            background: var(--bg-msg);
            padding: 12px 16px;
            border-radius: 8px;
            border-left: 3px solid transparent;
        }}
        .staff-row {{ border-left-color: var(--primary); }}
        .avatar {{ width: 42px; height: 42px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }}
        .msg-content {{ flex: 1; min-width: 0; }}
        .msg-header {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }}
        .author {{ font-weight: 600; color: #ffffff; }}
        .badge-staff {{
            background: var(--primary);
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
        }}
        .meta {{ font-size: 12px; color: var(--text-muted); }}
        .msg-body {{ word-wrap: break-word; font-size: 14px; }}
        .attachments {{ margin-top: 8px; display: flex; gap: 10px; flex-wrap: wrap; }}
        .attachments a {{
            color: #5865f2;
            text-decoration: none;
            font-size: 13px;
            background: #1e1f22;
            padding: 4px 10px;
            border-radius: 4px;
        }}
        .footer {{
            text-align: center;
            padding: 16px;
            font-size: 12px;
            color: var(--text-muted);
            border-top: 1px solid #313338;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{brand_name} • Transcript Ticket #{full_ticket.ticket_number:04d}</h1>
            <div class="meta-grid">
                <span><strong>Departamento:</strong> {dept_name}</span>
                <span><strong>Assunto:</strong> {subject}</span>
                <span><strong>Aberto por ID:</strong> {full_ticket.opener_id}</span>
                <span><strong>Responsável ID:</strong> {full_ticket.claimed_by_id or 'Não assumido'}</span>
                <span><strong>Status:</strong> {full_ticket.status.value.upper()}</span>
                <span><strong>Motivo Fechamento:</strong> {close_reason}</span>
            </div>
        </div>
        {rating_section}
        <div class="messages">
            {''.join(messages_html)}
        </div>
        <div class="footer">
            Gerado automaticamente pelo sistema {brand_name} em {generated_at} • Total de mensagens: {len(full_ticket.messages)}
        </div>
    </div>
</body>
</html>
"""

        guild_dir = settings.transcripts_dir / f"guild_{full_ticket.guild_id}"
        guild_dir.mkdir(parents=True, exist_ok=True)
        file_path = guild_dir / f"ticket_{full_ticket.ticket_number:04d}_{full_ticket.id}.html"

        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as fp:
            await fp.write(html_doc)

        full_ticket.transcript_path = str(file_path)
        return file_path