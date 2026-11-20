import enum
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, GuildScopedMixin, TimestampMixin


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    CLOSED = "closed"
    DELETED = "deleted"


class TicketPanel(Base, GuildScopedMixin, TimestampMixin):
    """Painéis de tickets configuráveis (suporta múltiplos painéis por servidor)."""

    __tablename__ = "ticket_panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color_hex: Mapped[str] = mapped_column(String(7), default="#FF4655", nullable=False)

    # Modo de exibição: "buttons" ou "dropdown"
    component_mode: Mapped[str] = mapped_column(String(20), default="dropdown", nullable=False)
    dropdown_placeholder: Mapped[str] = mapped_column(
        String(120),
        default="Selecione o departamento de atendimento...",
        nullable=False,
    )

    # Publicação no Discord
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_tickets_per_user: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    departments: Mapped[List["TicketDepartment"]] = relationship(
        back_populates="panel",
        cascade="all, delete-orphan",
        order_by="TicketDepartment.sort_order",
    )


class TicketDepartment(Base, GuildScopedMixin, TimestampMixin):
    """Departamentos vinculados a um painel de tickets."""

    __tablename__ = "ticket_departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("ticket_panels.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emoji: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    button_style: Mapped[str] = mapped_column(String(20), default="secondary", nullable=False)

    # Permissões e Canais do Departamento
    support_role_ids: Mapped[List[int]] = mapped_column(JSON, default=list, nullable=False)
    category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    log_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Mensagem inicial personalizada dentro do ticket
    welcome_title: Mapped[str] = mapped_column(String(200), default="Atendimento FUSION", nullable=False)
    welcome_description: Mapped[str] = mapped_column(
        Text,
        default="Olá {user}, descreva detalhadamente sua solicitação. Nossa equipe atenderá em breve.",
        nullable=False,
    )
    welcome_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    welcome_color_hex: Mapped[str] = mapped_column(String(7), default="#FF4655", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    panel: Mapped["TicketPanel"] = relationship(back_populates="departments")


class Ticket(Base, GuildScopedMixin, TimestampMixin):
    """Instância individual de um ticket aberto por um usuário."""

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("guild_id", "ticket_number", name="uq_guild_ticket_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    panel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ticket_panels.id", ondelete="SET NULL"), nullable=True)
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_departments.id", ondelete="SET NULL"),
        nullable=True,
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    opener_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    claimed_by_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    closed_by_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.OPEN, nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    close_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_user_ids: Mapped[List[int]] = mapped_column(JSON, default=list, nullable=False)
    transcript_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[List["TicketMessage"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )
    rating: Mapped[Optional["TicketRating"]] = relationship(
        back_populates="ticket",
        uselist=False,
        cascade="all, delete-orphan",
    )


class TicketMessage(Base, TimestampMixin):
    """Histórico persistente de mensagens do ticket para geração de transcript HTML."""

    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    discord_message_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    author_avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attachments_json: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    ticket: Mapped["Ticket"] = relationship(back_populates="messages")


class TicketRating(Base, GuildScopedMixin, TimestampMixin):
    """Avaliação de atendimento para métricas de desempenho da equipe."""

    __tablename__ = "ticket_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staff_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 a 5
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="rating")