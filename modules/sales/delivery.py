from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.embeds.factory import EmbedFactory
from core.logger import logger
from core.security import get_cipher
from database.models.guild import GuildConfig, LogCategory
from database.models.store import (
    DeliveryType,
    DigitalStockItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)
from database.models.vip import UserVip, VipPlan, VipStatus
from modules.logs.service import LogService


class DeliveryService:
    """Executa entregas automáticas e manuais com garantia de idempotência por item do pedido."""

    @staticmethod
    async def deliver_order(
        bot: discord.Client,
        session: AsyncSession,
        order_id: int,
        *,
        manual_actor: Optional[discord.abc.User] = None,
    ) -> tuple[bool, str]:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            return False, "Pedido não encontrado."

        if order.status == OrderStatus.DELIVERED:
            return True, "Pedido já entregue anteriormente."

        guild = bot.get_guild(order.guild_id)
        guild_cfg = await session.get(GuildConfig, order.guild_id)
        member: Optional[discord.Member] = guild.get_member(order.user_id) if guild else None

        # Tenta buscar membro via API caso não esteja no cache
        if guild and not member:
            try:
                member = await guild.fetch_member(order.user_id)
            except discord.HTTPException:
                member = None

        now = datetime.now(timezone.utc)
        cipher = get_cipher()
        delivered_summaries: list[str] = []
        pending_manual_items: list[str] = []

        # Converte reserva de estoque em baixa definitiva
        if order.stock_reserved:
            for item in order.items:
                if item.product_id:
                    prod = await session.get(Product, item.product_id, with_for_update=True)
                    if prod and prod.stock_enabled:
                        prod.reserved_quantity = max(0, prod.reserved_quantity - item.quantity)
                        prod.stock_quantity = max(0, prod.stock_quantity - item.quantity)
            order.stock_reserved = False

        for item in order.items:
            if item.is_delivered:
                continue

            if item.delivery_type == DeliveryType.DISCORD_ROLE:
                if guild and member and item.role_id:
                    role = guild.get_role(item.role_id)
                    if role:
                        try:
                            await member.add_roles(role, reason=f"Entrega Pedido {order.order_code}")
                            item.is_delivered = True
                            item.delivered_payload = f"Cargo {role.name} ({role.id}) atribuído."
                            delivered_summaries.append(f"🎭 **{item.product_name}** (`x{item.quantity}`) → Cargo {role.mention}")
                        except discord.Forbidden:
                            pending_manual_items.append(f"{item.product_name} (Sem permissão para atribuir cargo)")
                    else:
                        pending_manual_items.append(f"{item.product_name} (Cargo não encontrado)")
                else:
                    pending_manual_items.append(f"{item.product_name} (Membro fora do servidor)")

            elif item.delivery_type == DeliveryType.VIP_PLAN:
                plan: Optional[VipPlan] = None
                if item.vip_plan_id:
                    plan = await session.get(VipPlan, item.vip_plan_id)
                elif item.role_id:
                    plan = (
                        await session.execute(
                            select(VipPlan).where(
                                VipPlan.guild_id == order.guild_id,
                                VipPlan.role_id == item.role_id,
                                VipPlan.is_active.is_(True),
                            )
                        )
                    ).scalars().first()

                if plan:
                    total_days = (plan.duration_days * item.quantity) if plan.duration_days else None

                    # Verifica se o usuário já possui assinatura ativa do mesmo plano para estender validade
                    existing_vip = (
                        await session.execute(
                            select(UserVip).where(
                                UserVip.guild_id == order.guild_id,
                                UserVip.user_id == order.user_id,
                                UserVip.plan_id == plan.id,
                                UserVip.status == VipStatus.ACTIVE,
                            )
                        )
                    ).scalar_one_or_none()

                    if existing_vip:
                        if total_days is None or existing_vip.expires_at is None:
                            existing_vip.expires_at = None
                        else:
                            base_dt = max(now, existing_vip.expires_at)
                            existing_vip.expires_at = base_dt + timedelta(days=total_days)
                        existing_vip.notified_thresholds = []
                        expires_at = existing_vip.expires_at
                    else:
                        expires_at = (now + timedelta(days=total_days)) if total_days else None
                        session.add(
                            UserVip(
                                guild_id=order.guild_id,
                                user_id=order.user_id,
                                plan_id=plan.id,
                                order_id=order.id,
                                started_at=now,
                                expires_at=expires_at,
                                status=VipStatus.ACTIVE,
                            )
                        )

                    if guild and member:
                        role = guild.get_role(plan.role_id)
                        if role:
                            try:
                                await member.add_roles(role, reason=f"Ativação VIP Pedido {order.order_code}")
                            except discord.Forbidden:
                                logger.warning(f"[Guild {order.guild_id}] Sem permissão para cargo VIP {role.id}")

                    item.is_delivered = True
                    exp_label = expires_at.strftime("%d/%m/%Y %H:%M UTC") if expires_at else "Permanente"
                    item.delivered_payload = f"Plano VIP {plan.name} ativado ({exp_label})."
                    delivered_summaries.append(f"👑 **{item.product_name}** → VIP **{plan.name}** (`{exp_label}`)")

                    await LogService.dispatch(
                        bot,
                        session,
                        order.guild_id,
                        LogCategory.VIPS,
                        title="VIP Ativado via Compra",
                        description=f"Usuário <@{order.user_id}> recebeu o plano **{plan.name}**.",
                        fields=[
                            ("Pedido", f"`{order.order_code}`", True),
                            ("Validade", exp_label, True),
                        ],
                    )
                else:
                    pending_manual_items.append(f"{item.product_name} (Plano VIP não vinculado)")

            elif item.delivery_type == DeliveryType.DIGITAL_KEY:
                if item.product_id:
                    keys_stmt = (
                        select(DigitalStockItem)
                        .where(
                            DigitalStockItem.product_id == item.product_id,
                            DigitalStockItem.is_used.is_(False),
                        )
                        .limit(item.quantity)
                        .with_for_update()
                    )
                    stock_rows = (await session.execute(keys_stmt)).scalars().all()
                    if len(stock_rows) >= item.quantity:
                        decrypted_keys: list[str] = []
                        for row in stock_rows:
                            row.is_used = True
                            row.order_item_id = item.id
                            row.used_at = now
                            plain = cipher.decrypt(row.content_encrypted) or "[CHAVE_CRIPTOGRAFADA]"
                            decrypted_keys.append(plain)

                        item.is_delivered = True
                        item.delivered_payload = "\n".join(decrypted_keys)
                        keys_formatted = "\n".join(f"`{k}`" for k in decrypted_keys)
                        delivered_summaries.append(f"🔑 **{item.product_name}**:\n{keys_formatted}")
                    else:
                        pending_manual_items.append(f"{item.product_name} (Chaves insuficientes no estoque)")
                else:
                    pending_manual_items.append(f"{item.product_name} (Produto removido)")

            elif item.delivery_type == DeliveryType.MANUAL:
                if manual_actor is not None:
                    item.is_delivered = True
                    item.delivered_payload = f"Entregue manualmente por {manual_actor} ({manual_actor.id})"
                    delivered_summaries.append(f"📦 **{item.product_name}** (`x{item.quantity}`) → Entregue pela equipe")
                else:
                    pending_manual_items.append(f"{item.product_name} (`x{item.quantity}`) — Aguardando entrega manual")

        # Concede cargo global de Cliente se configurado
        if guild and member and guild_cfg and guild_cfg.customer_role_id:
            cust_role = guild.get_role(guild_cfg.customer_role_id)
            if cust_role and cust_role not in member.roles:
                try:
                    await member.add_roles(cust_role, reason=f"Cliente FUSION - Pedido {order.order_code}")
                except discord.Forbidden:
                    pass

        all_delivered = all(i.is_delivered for i in order.items)
        if all_delivered:
            order.status = OrderStatus.DELIVERED
            order.delivered_at = now
            if manual_actor:
                order.delivered_by_id = manual_actor.id
        else:
            order.status = OrderStatus.PARTIALLY_DELIVERED

        # Envia recibo detalhado na DM do cliente
        dm_target: Optional[discord.abc.User] = member
        if not dm_target:
            try:
                dm_target = await bot.fetch_user(order.user_id)
            except discord.HTTPException:
                dm_target = None

        if dm_target:
            desc_parts = [f"Seu pedido **`{order.order_code}`** foi confirmado!\n"]
            if delivered_summaries:
                desc_parts.append("**Itens Entregues Automaticamente:**\n" + "\n".join(delivered_summaries))
            if pending_manual_items:
                desc_parts.append(
                    "\n**Itens Pendentes de Entrega pela Equipe:**\n"
                    + "\n".join(f"• {p}" for p in pending_manual_items)
                    + "\n*Abra um ticket de Compras informando o código do pedido caso precise agilizar.*"
                )

            receipt_embed = EmbedFactory.success(
                guild_cfg,
                title=f"Comprovante & Entrega • {order.order_code}",
                description="\n".join(desc_parts),
            )
            try:
                await dm_target.send(embed=receipt_embed)
            except discord.Forbidden:
                logger.warning(f"Não foi possível enviar DM de entrega para usuário {order.user_id}")

        await LogService.dispatch(
            bot,
            session,
            order.guild_id,
            LogCategory.DELIVERIES,
            title=f"Entrega Processada • {order.order_code}",
            description=(
                f"Cliente: <@{order.user_id}>\n"
                f"Status Final: **{order.status.value.upper()}**"
            ),
            actor=manual_actor,
            fields=[
                ("Entregues", "\n".join(delivered_summaries)[:1024] or "Nenhum automático", False),
                ("Pendentes", "\n".join(pending_manual_items)[:1024] or "Nenhum", False),
            ],
        )

        return True, f"Entrega processada ({order.status.value})."