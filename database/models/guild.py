import enum
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, GuildScopedMixin, TimestampMixin


class LogCategory(str, enum.Enum):
    TICKETS = "tickets"
    SUPPORT = "support"
    SALES = "sales"
    PAYMENTS = "payments"
    REFUNDS = "refunds"
    DELIVERIES = "deliveries"
    VIPS = "vips"
    PRODUCTS = "products"
    SETTINGS = "settings"
    ADMIN_ACTIONS = "admin_actions"
    ERRORS = "errors"


class BotModule(str, enum.Enum):
    ADMIN_PANEL = "admin_panel"
    TICKETS = "tickets"
    SALES = "sales"
    MERCADOPAGO = "mercadopago"
    PRODUCTS = "products"
    VIPS = "vips"
    AUTOMATIONS = "automations"
    CSGO = "csgo"
    STATISTICS = "statistics"
    SECURITY = "security"


class GuildConfig(Base, TimestampMixin):
    """Configurações gerais e identidade visual de cada servidor Discord."""

    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Identidade Visual FUSION
    brand_name: Mapped[str] = mapped_column(String(80), default="FUSION", nullable=False)
    primary_color_hex: Mapped[str] = mapped_column(String(7), default="#FF4655", nullable=False)
    secondary_color_hex: Mapped[str] = mapped_column(String(7), default="#1E1F22", nullable=False)
    footer_text: Mapped[str] = mapped_column(
        String(200),
        default="FUSION • Comunidade CS:GO Legacy & Servidores",
        nullable=False,
    )
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cargos e Canais Principais
    customer_role_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    auto_role_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    staff_role_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    shop_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    shop_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Configurações Gerais
    currency: Mapped[str] = mapped_column(String(10), default="BRL", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo", nullable=False)
    rate_limit_seconds: Mapped[int] = mapped_column(Integer, default=3, nullable=False)


class GuildLogChannel(Base, GuildScopedMixin, TimestampMixin):
    """Mapeamento independente dos 11 canais de logs por servidor."""

    __tablename__ = "guild_log_channels"
    __table_args__ = (
        UniqueConstraint("guild_id", "category", name="uq_guild_log_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[LogCategory] = mapped_column(Enum(LogCategory), nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class GuildModulePermission(Base, GuildScopedMixin, TimestampMixin):
    """Controle de permissões granular por cargo ou usuário em cada módulo."""

    __tablename__ = "guild_module_permissions"
    __table_args__ = (
        UniqueConstraint("guild_id", "module", "target_type", "target_id", name="uq_guild_mod_perm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module: Mapped[BotModule] = mapped_column(Enum(BotModule), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "role" | "user"
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)