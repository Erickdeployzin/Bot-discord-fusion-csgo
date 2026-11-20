import io
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import discord
import qrcode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import get_settings
from core.logger import logger
from core.security import get_cipher
from database.models.guild import GuildConfig, LogCategory
from database.models.store import (
    Coupon,
    GuildMercadoPagoConfig,
    Order,
    OrderItem,
    OrderStatus,
    PaymentStatus,
    PaymentTransaction,
    Product,
)
from modules.cart.service import CartService
from modules.logs.service import LogService
from modules.mercadopago.service import MercadoPagoAPIError, MercadoPagoService
from modules.sales.delivery import DeliveryService
from utils.helpers import format_currency


def generate_qr_png_bytes(qr_code_text: str) -> io.BytesIO:
    """Gera imagem PNG do QR Code PIX em memória sem gravar arquivos temporários."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(qr_code_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class SalesService:
    """Gerencia checkout com bloqueio transacional de estoque, validação Mercado Pago e reembolsos."""

    @staticmethod
    async def checkout_cart(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        member: discord.Member,
    ) -> tuple[Optional[Order], Optional[PaymentTransaction], Optional[str]]:
        settings = get_settings()
        guild_cfg = await session.get(GuildConfig, guild.id)
        if guild_cfg and guild_cfg.maintenance_mode:
            return None, None, "🚧 A loja está temporariamente em **modo manutenção**."

        mp_cfg = await session.get(GuildMercadoPagoConfig, guild.id)
        if not mp_cfg or not mp_cfg.is_active:
            return None, None, "⚠️ O pagamento via Mercado Pago ainda não foi configurado neste servidor."

        access_token = get_cipher().decrypt(mp_cfg.access_token_encrypted)
        if not access_token:
            return None, None, "❌ Falha ao ler credenciais do Mercado Pago. Contate um administrador."

        summary = await CartService.get_summary(session, guild.id, member.id)
        if not summary.items:
            return None, None, "🧺 Seu carrinho está vazio."

        if summary.total <= Decimal("0.00"):
            return None, None, "⚠️ O valor total do pedido deve ser maior que R$ 0,00."

        # 1. Bloqueio transacional de estoque (SELECT ... FOR UPDATE)
        for item in summary.items:
            prod = await session.get(Product, item.product_id, with_for_update=True)
            if not prod or not prod.is_active:
                return None, None, f"❌ O produto **{item.product.name}** não está mais disponível."
            if prod.stock_enabled:
                avail = prod.available_stock or 0
                if item.quantity > avail:
                    return (
                        None,
                        None,
                        f"⚠️ Estoque insuficiente para **{prod.name}** (disponível: `{avail}`).",
                    )

        # 2. Reserva de estoque
        for item in summary.items:
            prod = await session.get(Product, item.product_id, with_for_update=True)
            if prod and prod.stock_enabled:
                prod.reserved_quantity += item.quantity

        if summary.coupon:
            coupon_locked = await session.get(Coupon, summary.coupon.id, with_for_update=True)
            if coupon_locked:
                coupon_locked.used_count += 1

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=mp_cfg.pix_expiration_minutes)
        order_code = f"FUS-{now.strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"

        order = Order(
            guild_id=guild.id,
            order_code=order_code,
            user_id=member.id,
            coupon_code=summary.coupon.code if summary.coupon else None,
            subtotal_amount=summary.subtotal,
            discount_amount=summary.discount,
            total_amount=summary.total,
            currency=guild_cfg.currency if guild_cfg else "BRL",
            status=OrderStatus.PENDING_PAYMENT,
            stock_reserved=True,
            expires_at=expires_at,
        )
        session.add(order)
        await session.flush()

        for item in summary.items:
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=item.product.id,
                    product_name=item.product.name,
                    unit_price=item.product.price,
                    quantity=item.quantity,
                    delivery_type=item.product.delivery_type,
                    role_id=item.product.role_id,
                    vip_plan_id=item.product.vip_plan_id,
                    duration_days=item.product.duration_days,
                )
            )
        await session.flush()

        # 3. Criação da cobrança PIX oficial na API do Mercado Pago
        idempotency_key = f"{order_code}-{secrets.token_hex(6)}"
        notification_url = f"{settings.public_base_url}/api/v1/webhooks/mercadopago/{guild.id}"
        description = f"Pedido {order_code} - {guild_cfg.brand_name if guild_cfg else 'FUSION'}"

        try:
            mp_data = await MercadoPagoService.create_pix_payment(
                access_token=access_token,
                idempotency_key=idempotency_key,
                order_code=order_code,
                amount=summary.total,
                description=description,
                payer_email=f"cliente_{member.id}@fusioncsgoservidores.uk",
                payer_first_name=member.display_name,
                expires_at=expires_at,
                notification_url=notification_url,
                metadata={
                    "guild_id": str(guild.id),
                    "order_code": order_code,
                    "discord_user_id": str(member.id),
                },
            )
        except MercadoPagoAPIError as exc:
            # Libera reserva imediatamente em caso de erro na API externa
            for item in summary.items:
                prod = await session.get(Product, item.product_id, with_for_update=True)
                if prod and prod.stock_enabled:
                    prod.reserved_quantity = max(0, prod.reserved_quantity - item.quantity)
            order.stock_reserved = False
            order.status = OrderStatus.CANCELLED
            logger.error(f"[Guild {guild.id}] Falha ao gerar PIX Mercado Pago: {exc}")
            return None, None, f"❌ Erro ao gerar cobrança PIX no Mercado Pago: `{exc.message}`"

        poi = mp_data.get("point_of_interaction", {})
        tx_data = poi.get("transaction_data", {})

        payment_tx = PaymentTransaction(
            guild_id=guild.id,
            order_id=order.id,
            mp_payment_id=str(mp_data["id"]),
            idempotency_key=idempotency_key,
            payment_method="pix",
            status=PaymentStatus.PENDING,
            status_detail=mp_data.get("status_detail"),
            amount=summary.total,
            currency=order.currency,
            qr_code=tx_data.get("qr_code"),
            qr_code_base64=tx_data.get("qr_code_base64"),
            ticket_url=tx_data.get("ticket_url"),
            raw_response=mp_data,
        )
        session.add(payment_tx)

        await CartService.clear_cart(session, guild.id, member.id)

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.SALES,
            title=f"Novo Pedido Gerado • {order.order_code}",
            description=f"Cliente {member.mention} iniciou pagamento PIX.",
            actor=member,
            fields=[
                ("Pedido", f"`{order.order_code}`", True),
                ("ID Mercado Pago", f"`{payment_tx.mp_payment_id}`", True),
                ("Total", f"**{format_currency(order.total_amount, order.currency)}**", True),
            ],
        )

        return order, payment_tx, None

    @staticmethod
    async def verify_and_approve_payment(
        bot: discord.Client,
        session: AsyncSession,
        guild_id: int,
        mp_payment_id: str,
    ) -> tuple[bool, str]:
        """
        Fluxo antifraude obrigatório:
        1. Consulta ativa diretamente na API oficial Mercado Pago (/v1/payments/{id}).
        2. Valida valor, moeda, external_reference e status == 'approved'.
        3. Garante idempotência para evitar entregas duplicadas.
        4. Dispara entrega automática.
        """
        mp_cfg = await session.get(GuildMercadoPagoConfig, guild_id)
        if not mp_cfg:
            return False, "Configuração Mercado Pago inexistente para este servidor."

        access_token = get_cipher().decrypt(mp_cfg.access_token_encrypted)
        if not access_token:
            return False, "Falha ao descriptografar Access Token."

        try:
            mp_payment = await MercadoPagoService.get_payment(access_token, str(mp_payment_id))
        except MercadoPagoAPIError as exc:
            logger.error(f"[Guild {guild_id}] Erro ao consultar pagamento MP {mp_payment_id}: {exc}")
            return False, f"Erro ao consultar API Mercado Pago: {exc.message}"

        status_raw = str(mp_payment.get("status", "")).lower()
        external_ref = str(mp_payment.get("external_reference") or "").strip()
        paid_amount = Decimal(str(mp_payment.get("transaction_amount", "0.00"))).quantize(Decimal("0.01"))
        currency_id = str(mp_payment.get("currency_id", "BRL")).upper()

        tx_stmt = (
            select(PaymentTransaction)
            .where(
                PaymentTransaction.guild_id == guild_id,
                PaymentTransaction.mp_payment_id == str(mp_payment_id),
            )
            .with_for_update()
        )
        payment_tx = (await session.execute(tx_stmt)).scalar_one_or_none()

        order: Optional[Order] = None
        if payment_tx:
            order = await session.get(Order, payment_tx.order_id, with_for_update=True)
        elif external_ref:
            order = (
                await session.execute(
                    select(Order)
                    .where(Order.guild_id == guild_id, Order.order_code == external_ref)
                    .with_for_update()
                )
            ).scalar_one_or_none()

        if not order:
            return False, f"Pedido correspondente ao pagamento `{mp_payment_id}` não encontrado."

        # Proteção antifraude: validação rigorosa de valor e moeda
        if paid_amount < order.total_amount:
            logger.warning(
                f"[FRAUDE/DIVERGÊNCIA] Pedido {order.order_code}: esperado {order.total_amount}, pago {paid_amount}"
            )
            return False, f"Divergência de valor detectada ({paid_amount} < {order.total_amount})."

        if currency_id != order.currency.upper():
            return False, f"Moeda divergente ({currency_id} != {order.currency})."

        if payment_tx:
            payment_tx.raw_response = mp_payment
            payment_tx.status_detail = mp_payment.get("status_detail")
            payment_tx.verified_at = datetime.now(timezone.utc)

        if status_raw != "approved":
            if payment_tx and status_raw in {e.value for e in PaymentStatus}:
                payment_tx.status = PaymentStatus(status_raw)
            return False, f"Pagamento ainda não aprovado na API oficial (status atual: `{status_raw}`)."

        # Idempotência: se já foi aprovado e entregue, não reexecuta
        if order.status in (OrderStatus.PAID, OrderStatus.DELIVERED):
            if order.status == OrderStatus.PAID:
                await DeliveryService.deliver_order(bot, session, order.id)
            return True, "Pagamento já aprovado e processado anteriormente."

        now = datetime.now(timezone.utc)
        if payment_tx:
            payment_tx.status = PaymentStatus.APPROVED
        order.status = OrderStatus.PAID
        order.paid_at = now

        await LogService.dispatch(
            bot,
            session,
            guild_id,
            LogCategory.PAYMENTS,
            title=f"Pagamento PIX Aprovado • {order.order_code}",
            description=f"Pagamento confirmado oficialmente pela API do Mercado Pago.",
            fields=[
                ("Pedido", f"`{order.order_code}`", True),
                ("ID Pagamento MP", f"`{mp_payment_id}`", True),
                ("Valor Pago", f"**{format_currency(paid_amount, currency_id)}**", True),
                ("Cliente", f"<@{order.user_id}> (`{order.user_id}`)", False),
            ],
        )

        await DeliveryService.deliver_order(bot, session, order.id)
        return True, "Pagamento aprovado e entrega concluída!"

    @staticmethod
    async def release_expired_orders(session: AsyncSession) -> int:
        """Libera reservas de estoque de pedidos pendentes cujo prazo PIX expirou."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(Order)
            .where(
                Order.status == OrderStatus.PENDING_PAYMENT,
                Order.expires_at <= now,
            )
            .options(selectinload(Order.items))
            .with_for_update()
        )
        expired_orders = (await session.execute(stmt)).scalars().all()
        count = 0

        for order in expired_orders:
            if order.stock_reserved:
                for item in order.items:
                    if item.product_id:
                        prod = await session.get(Product, item.product_id, with_for_update=True)
                        if prod and prod.stock_enabled:
                            prod.reserved_quantity = max(0, prod.reserved_quantity - item.quantity)
                order.stock_reserved = False
            order.status = OrderStatus.EXPIRED
            count += 1

        return count

    @staticmethod
    async def refund_order(
        bot: discord.Client,
        session: AsyncSession,
        guild_id: int,
        order_code: str,
        actor: discord.abc.User,
        reason: str = "Reembolso administrativo autorizado",
    ) -> tuple[bool, str]:
        stmt = (
            select(Order)
            .where(Order.guild_id == guild_id, Order.order_code == order_code.strip().upper())
            .options(selectinload(Order.payments))
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            return False, "❌ Pedido não encontrado."

        if order.status == OrderStatus.REFUNDED:
            return False, "⚠️ Este pedido já foi reembolsado."

        approved_tx = next(
            (p for p in order.payments if p.status == PaymentStatus.APPROVED),
            None,
        )
        if not approved_tx:
            return False, "❌ Nenhuma transação aprovada encontrada para reembolsar neste pedido."

        mp_cfg = await session.get(GuildMercadoPagoConfig, guild_id)
        if not mp_cfg:
            return False, "❌ Credenciais Mercado Pago não encontradas."

        access_token = get_cipher().decrypt(mp_cfg.access_token_encrypted)
        if not access_token:
            return False, "❌ Falha ao descriptografar Access Token."

        try:
            await MercadoPagoService.refund_payment(
                access_token=access_token,
                mp_payment_id=approved_tx.mp_payment_id,
                idempotency_key=f"refund-{order.order_code}-{secrets.token_hex(4)}",
            )
        except MercadoPagoAPIError as exc:
            return False, f"❌ Mercado Pago recusou o reembolso: `{exc.message}`"

        now = datetime.now(timezone.utc)
        approved_tx.status = PaymentStatus.REFUNDED
        approved_tx.refunded_amount = approved_tx.amount
        approved_tx.refunded_by_id = actor.id
        approved_tx.refunded_at = now
        order.status = OrderStatus.REFUNDED

        await LogService.dispatch(
            bot,
            session,
            guild_id,
            LogCategory.REFUNDS,
            title=f"Reembolso Executado • {order.order_code}",
            description=f"Reembolso total processado via API oficial Mercado Pago.",
            actor=actor,
            fields=[
                ("Pedido", f"`{order.order_code}`", True),
                ("ID Pagamento MP", f"`{approved_tx.mp_payment_id}`", True),
                ("Valor Estornado", format_currency(approved_tx.amount, order.currency), True),
                ("Motivo", reason, False),
            ],
        )

        return True, f"✅ Pedido **`{order.order_code}`** reembolsado com sucesso (`{format_currency(approved_tx.amount, order.currency)}`)."