import enum
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, GuildScopedMixin, TimestampMixin


class VipStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class VipPlan(Base, GuildScopedMixin, TimestampMixin):
    """Planos VIP configuráveis (Discord + integração com grupos SourceMod CS:GO)."""

    __tablename__ = "vip_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = Permanente
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    csgo_vip_flag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    benefits: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    notify_days_before: Mapped[List[int]] = mapped_column(JSON, default=lambda: [7, 3, 1], nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UserVip(Base, GuildScopedMixin, TimestampMixin):
    """Assinaturas VIP ativas e histórico de renovações/expirações."""

    __tablename__ = "user_vips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("vip_plans.id", ondelete="RESTRICT"), nullable=False)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    status: Mapped[VipStatus] = mapped_column(Enum(VipStatus), default=VipStatus.ACTIVE, index=True, nullable=False)
    notified_thresholds: Mapped[List[int]] = mapped_column(JSON, default=list, nullable=False)
    revoked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    plan: Mapped["VipPlan"] = relationship()


class UserSteamLink(Base, TimestampMixin):
    """Vínculo Discord <-> SteamID para ativação automática de VIP no CS:GO Legacy e Laravel."""

    __tablename__ = "user_steam_links"
    __table_args__ = (
        UniqueConstraint("discord_user_id", name="uq_discord_steam_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    steam_id64: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    steam_id2: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)