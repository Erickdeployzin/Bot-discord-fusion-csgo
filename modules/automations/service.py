from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.embeds.factory import EmbedFactory
from core.logger import logger
from database.models.automations import AutomatedMessage, AutomationTrigger
from database.models.guild import GuildConfig
from utils.helpers import parse_hex_color, render_template_variables


class AutomationExecutorService:
    """Processa envios periódicos, semanais e agendados das mensagens automáticas."""

    @staticmethod
    async def run_due_automations(bot: discord.Client, session: AsyncSession) -> int:
        now_utc = datetime.now(timezone.utc)
        stmt = select(AutomatedMessage).where(
            AutomatedMessage.is_active.is_(True),
            AutomatedMessage.trigger_type.in_(
                [
                    AutomationTrigger.PERIODIC_INTERVAL,
                    AutomationTrigger.WEEKLY_SCHEDULE,
                    AutomationTrigger.SCHEDULED_ONCE,
                ]
            ),
        )
        automations = (await session.execute(stmt)).scalars().all()
        sent_total = 0

        for auto in automations:
            if not auto.channel_id:
                continue
            guild = bot.get_guild(auto.guild_id)
            if not guild:
                continue
            channel = guild.get_channel(auto.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue

            guild_cfg = await session.get(GuildConfig, guild.id)
            tz_name = guild_cfg.timezone if guild_cfg else "America/Sao_Paulo"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("America/Sao_Paulo")

            local_now = now_utc.astimezone(tz)
            should_send = False

            if auto.trigger_type == AutomationTrigger.PERIODIC_INTERVAL:
                if auto.interval_minutes and auto.interval_minutes >= 1:
                    if not auto.last_sent_at:
                        should_send = True
                    else:
                        elapsed_minutes = (now_utc - auto.last_sent_at).total_seconds() / 60.0
                        if elapsed_minutes >= auto.interval_minutes:
                            should_send = True

            elif auto.trigger_type == AutomationTrigger.WEEKLY_SCHEDULE:
                if auto.schedule_time:
                    current_hhmm = local_now.strftime("%H:%M")
                    allowed_days = auto.weekdays if auto.weekdays else list(range(7))
                    already_sent_this_minute = bool(
                        auto.last_sent_at
                        and auto.last_sent_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
                        == local_now.strftime("%Y-%m-%d %H:%M")
                    )
                    if (
                        local_now.weekday() in allowed_days
                        and current_hhmm == auto.schedule_time
                        and not already_sent_this_minute
                    ):
                        should_send = True

            elif auto.trigger_type == AutomationTrigger.SCHEDULED_ONCE:
                if auto.run_at and auto.run_at <= now_utc and not auto.last_sent_at:
                    should_send = True
                    auto.is_active = False

            if not should_send:
                continue

            content = render_template_variables(auto.content_text, guild=guild, tz_name=tz_name)
            title = render_template_variables(auto.embed_title, guild=guild, tz_name=tz_name)
            desc = render_template_variables(auto.embed_description, guild=guild, tz_name=tz_name)

            embed = EmbedFactory.build(
                guild_cfg,
                title=title or auto.name,
                description=desc,
                color_override=parse_hex_color(auto.embed_color_hex),
                image_url=auto.embed_image_url,
                thumbnail_url=auto.embed_thumbnail_url,
            )
            if auto.embed_footer_text:
                embed.set_footer(text=auto.embed_footer_text)

            try:
                await channel.send(content=content or None, embed=embed)
                auto.last_sent_at = now_utc
                sent_total += 1
            except discord.Forbidden:
                logger.warning(f"[Guild {guild.id}] Sem permissão para disparar automação #{auto.id}")
            except Exception as exc:
                logger.error(f"[Guild {guild.id}] Falha ao disparar automação #{auto.id}: {exc}")

        return sent_total