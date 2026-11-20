import re
from typing import Optional

import discord
from sqlalchemy import select

from bot.embeds.factory import EmbedFactory
from core.database import get_db_session
from core.permissions import rate_limiter
from database.models.guild import GuildConfig
from database.models.store import Order, OrderStatus, PaymentTransaction, Product
from modules.cart.service import CartService
from modules.sales.service import SalesService, generate_qr_png_bytes
from utils.helpers import format_currency


# ==============================================================================
# MODALS DE QUANTIDADE E CUPOM NO CARRINHO
# ==============================================================================
class AddQuantityModal(discord.ui.Modal, title="Adicionar ao Carrinho"):
    quantity = discord.ui.TextInput(
        label="Quantidade desejada",
        default="1",
        max_length=3,
        required=True,
    )

    def __init__(self, product_id: int, product_name: str) -> None:
        super().__init__()
        self.product_id = product_id
        self.title = f"Comprar: {product_name[:32]}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            qty = int(self.quantity.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Informe um número inteiro válido.", ephemeral=True)
            return

        async with get_db_session() as session:
            ok, msg = await CartService.add_product(
                session,
                interaction.guild.id,
                interaction.user.id,
                self.product_id,
                qty,
            )
            if not ok:
                await interaction.response.send_message(msg, ephemeral=True)
                return

            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)

        await interaction.response.send_message(content=msg, embed=embed, view=view, ephemeral=True)


class ApplyCouponModal(discord.ui.Modal, title="Aplicar Cupom de Desconto"):
    coupon_code = discord.ui.TextInput(
        label="Código do Cupom",
        placeholder="Ex: FUSION10",
        max_length=40,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            ok, msg = await CartService.apply_coupon(
                session,
                interaction.guild.id,
                interaction.user.id,
                self.coupon_code.value,
            )
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)

        if interaction.response.is_done():
            await interaction.edit_original_response(content=msg, embed=embed, view=view)
        else:
            await interaction.response.send_message(content=msg, embed=embed, view=view, ephemeral=True)


# ==============================================================================
# DYNAMIC ITEMS PERSISTENTES DA VITRINE E DO PAGAMENTO PIX
# ==============================================================================
class StoreProductSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"fusion:shop:select:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int, options: Optional[list[discord.SelectOption]] = None) -> None:
        super().__init__(
            discord.ui.Select(
                placeholder="Escolha um produto para ver detalhes e adicionar ao carrinho...",
                min_values=1,
                max_values=1,
                options=options or [discord.SelectOption(label="Carregando catálogo...", value="0")],
                custom_id=f"fusion:shop:select:{guild_id}",
                row=0,
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
    ) -> "StoreProductSelect":
        return cls(guild_id=int(match["guild_id"]), options=list(item.options))

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        val = self.item.values[0]
        if not val.isdigit() or int(val) == 0:
            await interaction.response.send_message("⚠️ Nenhum produto selecionado.", ephemeral=True)
            return

        product_id = int(val)
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            product = await session.get(Product, product_id)
            if not product or product.guild_id != interaction.guild.id or not product.is_active:
                await interaction.response.send_message("❌ Este produto não está mais disponível.", ephemeral=True)
                return

            currency = cfg.currency if cfg else "BRL"
            stock_txt = (
                f"`{product.available_stock}` unidade(s)"
                if product.stock_enabled
                else "`Ilimitado`"
            )
            embed = EmbedFactory.build(
                cfg,
                title=f"🏷️ {product.name}",
                description=product.description,
                image_url=product.image_url,
            )
            embed.add_field(name="Preço Unitário", value=f"**{format_currency(product.price, currency)}**", inline=True)
            embed.add_field(name="Categoria", value=f"`{product.category}`", inline=True)
            embed.add_field(name="Estoque", value=stock_txt, inline=True)

        await interaction.response.send_message(
            embed=embed,
            view=ProductQuickAddView(product.id, product.name),
            ephemeral=True,
        )


class StoreOpenCartButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:shop:cart:(?P<guild_id>\d+)",
):
    def __init__(self, guild_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Meu Carrinho",
                emoji="🧺",
                style=discord.ButtonStyle.primary,
                custom_id=f"fusion:shop:cart:{guild_id}",
                row=1,
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "StoreOpenCartButton":
        return cls(guild_id=int(match["guild_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class PixCopyCodeButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:pix:copy:(?P<tx_id>\d+)",
):
    def __init__(self, tx_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="PIX Copia e Cola",
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"fusion:pix:copy:{tx_id}",
            )
        )
        self.tx_id = tx_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "PixCopyCodeButton":
        return cls(tx_id=int(match["tx_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        async with get_db_session() as session:
            tx = await session.get(PaymentTransaction, self.tx_id)
            if not tx or not tx.qr_code:
                await interaction.response.send_message("❌ Código PIX não encontrado.", ephemeral=True)
                return
            code = tx.qr_code

        await interaction.response.send_message(
            content=f"📋 **Copie o código PIX abaixo e cole no aplicativo do seu banco:**\n```\n{code}\n```",
            ephemeral=True,
        )


class PixCheckStatusButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"fusion:pix:check:(?P<guild_id>\d+):(?P<mp_id>[0-9]+)",
):
    def __init__(self, guild_id: int, mp_payment_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Já Paguei (Verificar API)",
                emoji="🔄",
                style=discord.ButtonStyle.success,
                custom_id=f"fusion:pix:check:{guild_id}:{mp_payment_id}",
            )
        )
        self.guild_id = guild_id
        self.mp_payment_id = mp_payment_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ) -> "PixCheckStatusButton":
        return cls(guild_id=int(match["guild_id"]), mp_payment_id=match["mp_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        allowed, retry = rate_limiter.check(interaction.user.id, "check_pix", max_calls=2, window_seconds=10.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Aguarde `{retry}s` antes de consultar o Mercado Pago novamente.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with get_db_session() as session:
            ok, detail = await SalesService.verify_and_approve_payment(
                bot=interaction.client,
                session=session,
                guild_id=self.guild_id,
                mp_payment_id=self.mp_payment_id,
            )

        if ok:
            await interaction.followup.send(
                f"✅ **{detail}** Verifique sua DM para acessar os detalhes da entrega!",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⏳ {detail}\n*Assim que o banco compensar o PIX, a aprovação e entrega ocorrem automaticamente.*",
                ephemeral=True,
            )


# ==============================================================================
# VIEWS DA VITRINE, PRODUTO, CARRINHO E CHECKOUT PIX
# ==============================================================================
class StorefrontView(discord.ui.View):
    """Vitrine pública da loja (compatível com SalesCategoryView da Etapa 2)."""

    def __init__(self, guild_id: int, products: list[Product]) -> None:
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(
                label=f"{p.name} — {format_currency(p.price)}"[:100],
                value=str(p.id),
                description=(p.description[:95] if p.description else f"Categoria: {p.category}"),
            )
            for p in products[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum produto disponível no momento", value="0")]

        self.add_item(StoreProductSelect(guild_id, options))
        self.add_item(StoreOpenCartButton(guild_id))


class ProductQuickAddView(discord.ui.View):
    def __init__(self, product_id: int, product_name: str) -> None:
        super().__init__(timeout=180)
        self.product_id = product_id
        self.product_name = product_name

    @discord.ui.button(label="Adicionar 1x ao Carrinho", style=discord.ButtonStyle.success, emoji="➕")
    async def add_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            ok, msg = await CartService.add_product(
                session,
                interaction.guild.id,
                interaction.user.id,
                self.product_id,
                1,
            )
            if not ok:
                await interaction.response.send_message(msg, ephemeral=True)
                return
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)

        await interaction.response.edit_message(content=msg, embed=embed, view=view)

    @discord.ui.button(label="Escolher Quantidade", style=discord.ButtonStyle.primary, emoji="🔢")
    async def choose_qty(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddQuantityModal(self.product_id, self.product_name))


class CartRemoveItemSelect(discord.ui.Select):
    def __init__(self, items: list) -> None:
        options = [
            discord.SelectOption(
                label=f"Remover: {item.product.name} (x{item.quantity})"[:100],
                value=str(item.product_id),
            )
            for item in items[:25]
        ]
        super().__init__(placeholder="Remover item do carrinho...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        pid = int(self.values[0])
        async with get_db_session() as session:
            await CartService.set_item_quantity(session, interaction.guild.id, interaction.user.id, pid, 0)
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)


class CartView(discord.ui.View):
    def __init__(self, items: list) -> None:
        super().__init__(timeout=300)
        if items:
            self.add_item(CartRemoveItemSelect(items))

    @discord.ui.button(label="Finalizar Compra (PIX)", style=discord.ButtonStyle.success, emoji="💳", row=1)
    async def checkout_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        allowed, retry = rate_limiter.check(interaction.user.id, "checkout_pix", max_calls=2, window_seconds=20.0)
        if not allowed:
            await interaction.response.send_message(
                f"⏳ Aguarde `{retry}s` antes de gerar outro pagamento.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            order, tx, err = await SalesService.checkout_cart(
                interaction.client,
                session,
                interaction.guild,
                interaction.user,
            )

        if err or not order or not tx:
            await interaction.followup.send(err or "❌ Não foi possível gerar o pedido.", ephemeral=True)
            return

        currency = cfg.currency if cfg else "BRL"
        pix_embed = EmbedFactory.build(
            cfg,
            title=f"💠 Pagamento PIX • Pedido {order.order_code}",
            description=(
                "Escaneie o **QR Code** abaixo com o aplicativo do seu banco ou clique em **PIX Copia e Cola**.\n\n"
                f"• **Valor Total:** **{format_currency(order.total_amount, currency)}**\n"
                f"• **Expira em:** <t:{int(order.expires_at.timestamp())}:R>\n"
                "• **Entrega:** 100% automática após confirmação bancária."
            ),
            image_url="attachment://qrcode_pix.png" if tx.qr_code else None,
        )

        pix_view = PixPaymentView(interaction.guild.id, tx.id, tx.mp_payment_id)

        if tx.qr_code:
            qr_buffer = generate_qr_png_bytes(tx.qr_code)
            qr_file = discord.File(qr_buffer, filename="qrcode_pix.png")
            await interaction.followup.send(embed=pix_embed, file=qr_file, view=pix_view, ephemeral=True)
        else:
            await interaction.followup.send(embed=pix_embed, view=pix_view, ephemeral=True)

    @discord.ui.button(label="Aplicar Cupom", style=discord.ButtonStyle.primary, emoji="🎟️", row=1)
    async def coupon_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ApplyCouponModal())

    @discord.ui.button(label="Esvaziar Carrinho", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            await CartService.clear_cart(session, interaction.guild.id, interaction.user.id)
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)
        await interaction.response.edit_message(content="🧹 Carrinho esvaziado.", embed=embed, view=view)


class PixPaymentView(discord.ui.View):
    def __init__(self, guild_id: int, tx_id: int, mp_payment_id: str) -> None:
        super().__init__(timeout=None)
        self.add_item(PixCopyCodeButton(tx_id))
        self.add_item(PixCheckStatusButton(guild_id, mp_payment_id))


async def build_cart_embed_and_view(
    session,
    guild: discord.Guild,
    user_id: int,
) -> tuple[discord.Embed, CartView]:
    cfg = await session.get(GuildConfig, guild.id)
    currency = cfg.currency if cfg else "BRL"
    summary = await CartService.get_summary(session, guild.id, user_id)

    if not summary.items:
        embed = EmbedFactory.build(
            cfg,
            title="🧺 Seu Carrinho FUSION",
            description="Seu carrinho está vazio no momento. Selecione produtos na vitrine da loja ou via `/loja`.",
        )
        return embed, CartView([])

    lines = [
        f"• **{item.product.name}** (`x{item.quantity}`) — **{format_currency(item.product.price * item.quantity, currency)}**"
        for item in summary.items
    ]
    embed = EmbedFactory.build(
        cfg,
        title="🧺 Seu Carrinho FUSION",
        description="\n".join(lines),
    )
    embed.add_field(name="Subtotal", value=format_currency(summary.subtotal, currency), inline=True)
    embed.add_field(
        name="Desconto",
        value=(
            f"-{format_currency(summary.discount, currency)} (`{summary.coupon.code}`)"
            if summary.coupon
            else format_currency(Decimal("0.00"), currency)
        ),
        inline=True,
    )
    embed.add_field(name="Total a Pagar", value=f"**{format_currency(summary.total, currency)}**", inline=True)
    return embed, CartView(summary.items)