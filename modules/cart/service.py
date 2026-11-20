from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models.store import Cart, CartItem, Coupon, Product


@dataclass
class CartSummary:
    cart: Cart
    items: list[CartItem]
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    coupon: Optional[Coupon]


class CartService:
    """Gerencia carrinhos individuais persistidos no banco com cálculo de descontos e validação de estoque."""

    @staticmethod
    async def get_or_create_cart(session: AsyncSession, guild_id: int, user_id: int) -> Cart:
        stmt = (
            select(Cart)
            .where(Cart.guild_id == guild_id, Cart.user_id == user_id)
            .options(
                selectinload(Cart.items).selectinload(CartItem.product),
                selectinload(Cart.coupon),
            )
        )
        cart = (await session.execute(stmt)).scalar_one_or_none()
        if not cart:
            cart = Cart(guild_id=guild_id, user_id=user_id)
            session.add(cart)
            await session.flush()
            cart = (await session.execute(stmt)).scalar_one()
        return cart

    @staticmethod
    async def add_product(
        session: AsyncSession,
        guild_id: int,
        user_id: int,
        product_id: int,
        quantity: int = 1,
    ) -> tuple[bool, str]:
        if quantity < 1:
            return False, "❌ A quantidade deve ser pelo menos `1`."

        product = await session.get(Product, product_id)
        if not product or product.guild_id != guild_id or not product.is_active:
            return False, "❌ Produto indisponível ou não encontrado."

        cart = await CartService.get_or_create_cart(session, guild_id, user_id)
        existing_item = next((item for item in cart.items if item.product_id == product_id), None)
        new_qty = (existing_item.quantity if existing_item else 0) + quantity

        if new_qty > product.max_per_order:
            return False, f"⚠️ Limite máximo de **{product.max_per_order}** unidade(s) deste produto por compra."

        if product.stock_enabled:
            avail = product.available_stock or 0
            if new_qty > avail:
                return False, f"⚠️ Estoque insuficiente. Disponível no momento: **{avail}** unidade(s)."

        if existing_item:
            existing_item.quantity = new_qty
        else:
            session.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity))

        await session.flush()
        return True, f"✅ **{product.name}** (`x{new_qty}`) atualizado no seu carrinho!"

    @staticmethod
    async def set_item_quantity(
        session: AsyncSession,
        guild_id: int,
        user_id: int,
        product_id: int,
        quantity: int,
    ) -> tuple[bool, str]:
        cart = await CartService.get_or_create_cart(session, guild_id, user_id)
        item = next((i for i in cart.items if i.product_id == product_id), None)
        if not item:
            return False, "❌ Este item não está no seu carrinho."

        if quantity <= 0:
            await session.delete(item)
            await session.flush()
            return True, "🗑️ Item removido do carrinho."

        product = item.product
        if quantity > product.max_per_order:
            return False, f"⚠️ Limite máximo por pedido: **{product.max_per_order}**."

        if product.stock_enabled:
            avail = product.available_stock or 0
            if quantity > avail:
                return False, f"⚠️ Estoque disponível: **{avail}** unidade(s)."

        item.quantity = quantity
        await session.flush()
        return True, f"✅ Quantidade de **{product.name}** ajustada para `{quantity}`."

    @staticmethod
    async def clear_cart(session: AsyncSession, guild_id: int, user_id: int) -> None:
        cart = await CartService.get_or_create_cart(session, guild_id, user_id)
        await session.execute(delete(CartItem).where(CartItem.cart_id == cart.id))
        cart.coupon_id = None
        await session.flush()

    @staticmethod
    async def apply_coupon(
        session: AsyncSession,
        guild_id: int,
        user_id: int,
        raw_code: str,
    ) -> tuple[bool, str]:
        code = raw_code.strip().upper()
        coupon = (
            await session.execute(
                select(Coupon).where(Coupon.guild_id == guild_id, Coupon.code == code)
            )
        ).scalar_one_or_none()

        if not coupon or not coupon.is_active:
            return False, "❌ Cupom inválido ou inativo."

        now = datetime.now(timezone.utc)
        if coupon.expires_at and coupon.expires_at <= now:
            return False, "⏳ Este cupom já expirou."

        if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
            return False, "⚠️ Este cupom atingiu o limite máximo de utilizações."

        cart = await CartService.get_or_create_cart(session, guild_id, user_id)
        summary = await CartService.get_summary(session, guild_id, user_id)
        if summary.subtotal < coupon.min_order_value:
            return (
                False,
                f"⚠️ Valor mínimo do carrinho para usar `{code}`: **R$ {coupon.min_order_value:.2f}**.",
            )

        cart.coupon_id = coupon.id
        await session.flush()
        return True, f"🎟️ Cupom **{code}** aplicado ao seu carrinho!"

    @staticmethod
    async def get_summary(session: AsyncSession, guild_id: int, user_id: int) -> CartSummary:
        cart = await CartService.get_or_create_cart(session, guild_id, user_id)
        valid_items = [item for item in cart.items if item.product and item.product.is_active]

        subtotal = sum(
            (item.product.price * item.quantity for item in valid_items),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

        discount = Decimal("0.00")
        active_coupon: Optional[Coupon] = None

        if cart.coupon and cart.coupon.is_active:
            now = datetime.now(timezone.utc)
            not_expired = not cart.coupon.expires_at or cart.coupon.expires_at > now
            within_uses = cart.coupon.max_uses is None or cart.coupon.used_count < cart.coupon.max_uses
            meets_min = subtotal >= cart.coupon.min_order_value

            if not_expired and within_uses and meets_min and subtotal > Decimal("0.00"):
                active_coupon = cart.coupon
                if active_coupon.discount_type == "percent":
                    discount = (subtotal * (active_coupon.discount_value / Decimal("100"))).quantize(Decimal("0.01"))
                else:
                    discount = min(subtotal, active_coupon.discount_value).quantize(Decimal("0.01"))

        total = max(Decimal("0.00"), subtotal - discount).quantize(Decimal("0.01"))
        return CartSummary(
            cart=cart,
            items=valid_items,
            subtotal=subtotal,
            discount=discount,
            total=total,
            coupon=active_coupon,
        )