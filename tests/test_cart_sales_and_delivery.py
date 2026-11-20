from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from core.security import get_cipher
from database.models.guild import GuildConfig
from database.models.store import (
    Coupon,
    DeliveryType,
    DigitalStockItem,
    GuildMercadoPagoConfig,
    Order,
    OrderStatus,
    Product,
)
from database.models.vip import UserVip, VipPlan, VipStatus
from modules.cart.service import CartService
from modules.sales.service import SalesService


@pytest.mark.asyncio
async def test_cart_coupon_and_transactional_checkout_with_auto_delivery(
    db_session,
    mock_discord_env,
) -> None:
    bot, guild, member = mock_discord_env
    cipher = get_cipher()

    # 1. Configura servidor, Mercado Pago, Plano VIP, Produto e Cupom
    db_session.add(
        GuildConfig(
            guild_id=guild.id,
            guild_name=guild.name,
            customer_role_id=444555666777888999,
        )
    )
    db_session.add(
        GuildMercadoPagoConfig(
            guild_id=guild.id,
            access_token_encrypted=cipher.encrypt("APP_USR-TEST-TOKEN"),
            pix_expiration_minutes=30,
            is_active=True,
        )
    )

    vip_plan = VipPlan(
        guild_id=guild.id,
        name="VIP Diamante",
        description="Plano VIP completo CS:GO Legacy",
        price=Decimal("40.00"),
        duration_days=30,
        role_id=555666777888999000,
        csgo_vip_flag="vip_diamante",
        is_active=True,
    )
    db_session.add(vip_plan)
    await db_session.flush()

    vip_product = Product(
        guild_id=guild.id,
        name="VIP Diamante 30 Dias",
        description="Acesso VIP imediato",
        price=Decimal("40.00"),
        category="VIPs",
        stock_enabled=True,
        stock_quantity=5,
        max_per_order=2,
        delivery_type=DeliveryType.VIP_PLAN,
        vip_plan_id=vip_plan.id,
        role_id=vip_plan.role_id,
        is_active=True,
    )
    key_product = Product(
        guild_id=guild.id,
        name="Chave Skin Exclusiva",
        description="Código digital",
        price=Decimal("10.00"),
        category="Skins",
        stock_enabled=True,
        stock_quantity=1,
        max_per_order=1,
        delivery_type=DeliveryType.DIGITAL_KEY,
        is_active=True,
    )
    db_session.add_all([vip_product, key_product])
    await db_session.flush()

    db_session.add(
        DigitalStockItem(
            guild_id=guild.id,
            product_id=key_product.id,
            content_encrypted=cipher.encrypt("FUSION-KEY-9999-ABCD"),
        )
    )
    db_session.add(
        Coupon(
            guild_id=guild.id,
            code="FUSION10",
            discount_type="percent",
            discount_value=Decimal("10.00"),
            min_order_value=Decimal("20.00"),
            is_active=True,
        )
    )
    await db_session.flush()

    # 2. Adiciona produtos ao carrinho e aplica cupom (Subtotal: 50.00 -> Desconto 10%: 5.00 -> Total: 45.00)
    ok1, _ = await CartService.add_product(db_session, guild.id, member.id, vip_product.id, 1)
    ok2, _ = await CartService.add_product(db_session, guild.id, member.id, key_product.id, 1)
    ok_cpn, _ = await CartService.apply_coupon(db_session, guild.id, member.id, "fusion10")
    assert ok1 and ok2 and ok_cpn

    summary = await CartService.get_summary(db_session, guild.id, member.id)
    assert summary.subtotal == Decimal("50.00")
    assert summary.discount == Decimal("5.00")
    assert summary.total == Decimal("45.00")

    # 3. Simula criação do PIX na API oficial Mercado Pago
    mock_mp_create = AsyncMock(
        return_value={
            "id": 7788990011,
            "status": "pending",
            "status_detail": "pending_waiting_transfer",
            "point_of_interaction": {
                "transaction_data": {
                    "qr_code": "00020126580014br.gov.bcb.pix0136fusion-pix-test",
                    "qr_code_base64": "iVBORw0KGgo=",
                }
            },
        }
    )

    with patch("modules.mercadopago.service.MercadoPagoService.create_pix_payment", mock_mp_create):
        order, tx, err = await SalesService.checkout_cart(bot, db_session, guild, member)

    assert err is None
    assert order is not None
    assert tx is not None
    assert order.total_amount == Decimal("45.00")
    assert vip_product.reserved_quantity == 1
    assert key_product.reserved_quantity == 1

    # 4. Simula confirmação oficial do pagamento pelo Mercado Pago e valida entrega automática
    mock_mp_get = AsyncMock(
        return_value={
            "id": 7788990011,
            "status": "approved",
            "status_detail": "accredited",
            "transaction_amount": 45.00,
            "currency_id": "BRL",
            "external_reference": order.order_code,
        }
    )

    with patch("modules.mercadopago.service.MercadoPagoService.get_payment", mock_mp_get):
        approved, detail = await SalesService.verify_and_approve_payment(
            bot, db_session, guild.id, "7788990011"
        )

    assert approved is True
    assert order.status == OrderStatus.DELIVERED
    assert vip_product.stock_quantity == 4
    assert vip_product.reserved_quantity == 0
    assert key_product.stock_quantity == 0
    assert key_product.reserved_quantity == 0

    # Valida ativação do VIP no banco
    active_vip = (
        await db_session.execute(
            select(UserVip).where(UserVip.user_id == member.id, UserVip.status == VipStatus.ACTIVE)
        )
    ).scalar_one()
    assert active_vip.plan_id == vip_plan.id
    assert active_vip.expires_at is not None

    # Valida que os cargos Discord foram atribuídos e o recibo foi enviado via DM
    assert member.add_roles.call_count >= 2
    member.send.assert_called()


@pytest.mark.asyncio
async def test_expired_order_releases_reserved_stock(db_session) -> None:
    prod = Product(
        guild_id=12345,
        name="Slot MIX",
        description="Teste reserva",
        price=Decimal("15.00"),
        stock_enabled=True,
        stock_quantity=10,
        reserved_quantity=3,
        delivery_type=DeliveryType.MANUAL,
        is_active=True,
    )
    db_session.add(prod)
    await db_session.flush()

    from database.models.store import OrderItem

    expired_order = Order(
        guild_id=12345,
        order_code="FUS-EXPIRED-01",
        user_id=999,
        subtotal_amount=Decimal("45.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("45.00"),
        status=OrderStatus.PENDING_PAYMENT,
        stock_reserved=True,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    db_session.add(expired_order)
    await db_session.flush()

    db_session.add(
        OrderItem(
            order_id=expired_order.id,
            product_id=prod.id,
            product_name=prod.name,
            unit_price=prod.price,
            quantity=3,
            delivery_type=DeliveryType.MANUAL,
        )
    )
    await db_session.flush()

    released = await SalesService.release_expired_orders(db_session)
    assert released == 1
    assert expired_order.status == OrderStatus.EXPIRED
    assert prod.reserved_quantity == 0
    assert prod.stock_quantity == 10