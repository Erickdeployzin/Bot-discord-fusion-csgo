import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, GuildScopedMixin, TimestampMixin


class AutomationTrigger(str, enum.Enum):
    MEMBER_JOIN = "member_join"
    MEMBER_LEAVE = "member_leave"
    SCHEDULED_ONCE = "scheduled_once"
    PERIODIC_INTERVAL = "periodic_interval"
    WEEKLY_SCHEDULE = "weekly_schedule"
    MANUAL = "manual"


class AutomatedMessage(Base, GuildScopedMixin, TimestampMixin):
    """Editor completo de mensagens automáticas e programadas configuráveis pelo Discord."""

    __tablename__ = "automated_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger_type: Mapped[AutomationTrigger] = mapped_column(Enum(AutomationTrigger), nullable=False)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Conteúdo e Embed Personalizável (suporta {user}, {username}, {server}, {member_count}, {date}, {time})
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embed_title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    embed_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embed_color_hex: Mapped[str] = mapped_column(String(7), default="#FF4655", nullable=False)
    embed_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embed_thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embed_footer_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    include_timestamp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Componentes Interativos configurados via painel
    buttons_config: Mapped[List[dict]] = mapped_column(JSON, default=list, nullable=False)
    dropdown_config: Mapped[List[dict]] = mapped_column(JSON, default=list, nullable=False)

    # Agendamento
    schedule_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # "HH:MM"
    interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weekdays: Mapped[List[int]] = mapped_column(JSON, default=list, nullable=False)  # 0=Seg .. 6=Dom
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)