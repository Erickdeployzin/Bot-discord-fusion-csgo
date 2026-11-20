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


class DeliveryType(str, enum.Enum):
    DISCORD_ROLE = "discord_role"
    VIP_PLAN = "vip_plan"
    DIGITAL_KEY = "digital_key"
    MANUAL = "manual"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    DELIVERED = "delivered"
    PARTIALLY_DELIVERED = "partially_delivered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    AUTHORIZED = "authorized"
    IN_PROCESS = "in_process"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    CHARGED_BACK = "charged_back"


class GuildMercadoPagoConfig(Base, TimestampMixin):
    """Credenciais criptografadas do Mercado Pago isoladas por servidor."""

    __tablename__ = "guild_mercadopago_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_sandbox: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pix_expiration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Product(Base, GuildScopedMixin, TimestampMixin):
    """Produtos cadastrados diretamente pelo painel administrativo do Discord."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(60), default="Geral", index=True, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    # Controle de Estoque
    stock_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_per_order: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    # Status e Entrega Automática
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delivery_type: Mapped[DeliveryType] = mapped_column(
        Enum(DeliveryType),
        default=DeliveryType.DISCORD_ROLE,
        nullable=False,
    )
    role_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    vip_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vip_plans.id", ondelete="SET NULL"), nullable=True)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivery_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def available_stock(self) -> Optional[int]:
        if not self.stock_enabled:
            return None
        return max(0, self.stock_quantity - self.reserved_quantity)


class DigitalStockItem(Base, GuildScopedMixin, TimestampMixin):
    """Pool de chaves/códigos digitais criptografados para entrega automática."""

    __tablename__ = "digital_stock_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    content_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    order_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Coupon(Base, GuildScopedMixin, TimestampMixin):
    """Cupons promocionais aplicáveis no carrinho."""

    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("guild_id", "code", name="uq_guild_coupon_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(16), default="percent", nullable=False)  # "percent" | "fixed"
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_order_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Cart(Base, GuildScopedMixin, TimestampMixin):
    """Carrinho individual persistente por usuário e servidor."""

    __tablename__ = "carts"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", name="uq_guild_user_cart"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    coupon_id: Mapped[Optional[int]] = mapped_column(ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True)
    channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    items: Mapped[List["CartItem"]] = relationship(
        back_populates="cart",
        cascade="all, delete-orphan",
    )
    coupon: Mapped[Optional["Coupon"]] = relationship()


class CartItem(Base, TimestampMixin):
    """Itens adicionados ao carrinho."""

    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    cart: Mapped["Cart"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


class Order(Base, GuildScopedMixin, TimestampMixin):
    """Pedidos gerados a partir do carrinho ou compra direta."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    coupon_code: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    subtotal_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="BRL", nullable=False)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus),
        default=OrderStatus.PENDING_PAYMENT,
        index=True,
        nullable=False,
    )
    stock_reserved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_by_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    delivery_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )
    payments: Mapped[List["PaymentTransaction"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
    )


class OrderItem(Base, TimestampMixin):
    """Snapshot imutável dos produtos no momento da criação do pedido."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    product_name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_type: Mapped[DeliveryType] = mapped_column(Enum(DeliveryType), nullable=False)
    role_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    vip_plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivered_payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")


class PaymentTransaction(Base, GuildScopedMixin, TimestampMixin):
    """Transações financeiras Mercado Pago com garantia de idempotência."""

    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False)
    mp_payment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    payment_method: Mapped[str] = mapped_column(String(32), default="pix", nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    status_detail: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="BRL", nullable=False)

    # Dados PIX
    qr_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qr_code_base64: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Auditoria e Reembolso
    raw_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"), nullable=False)
    refunded_by_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="payments")