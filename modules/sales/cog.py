import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.embeds.factory import EmbedFactory
from core.database import get_db_session
from core.permissions import has_module_permission
from database.models.guild import BotModule, GuildConfig
from database.models.store import Order, Product
from modules.sales.delivery import DeliveryService
from modules.sales.service import SalesService
from modules.sales.views import (
    PixCheckStatusButton,
    PixCopyCodeButton,
    StoreOpenCartButton,
    StoreProductSelect,
    StorefrontView,
    build_cart_embed_and_view,
)
from utils.helpers import format_currency


class SalesCog(commands.Cog):
    """Comandos de loja, carrinho individual, entrega manual e reembolsos autorizados."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(
            StoreProductSelect,
            StoreOpenCartButton,
            PixCopyCodeButton,
            PixCheckStatusButton,
        )

    @app_commands.command(name="loja", description="Abre o catálogo oficial de produtos e VIPs da FUSION.")
    @app_commands.guild_only()
    async def shop_command(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            products = (
                await session.execute(
                    select(Product)
                    .where(Product.guild_id == interaction.guild.id, Product.is_active.is_(True))
                    .order_by(Product.sort_order, Product.id)
                )
            ).scalars().all()

            embed = EmbedFactory.build(
                cfg,
                title=f"🛒 Loja Oficial • {cfg.brand_name if cfg else 'FUSION'}",
                description=(
                    "Explore nossos planos VIP e serviços abaixo.\n"
                    "Selecione um item no menu para ver detalhes e adicioná-lo ao seu carrinho."
                ),
                use_guild_banner=True,
            )
            view = StorefrontView(interaction.guild.id, list(products))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="carrinho", description="Visualiza e gerencia seu carrinho de compras.")
    @app_commands.guild_only()
    async def cart_command(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            embed, view = await build_cart_embed_and_view(session, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="pedido", description="Consulta o status e itens de um pedido pelo código.")
    @app_commands.describe(codigo="Código do pedido (Ex: FUS-261120-A1B2C3)")
    @app_commands.guild_only()
    async def order_lookup(self, interaction: discord.Interaction, codigo: str) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            stmt = (
                select(Order)
                .where(
                    Order.guild_id == interaction.guild.id,
                    Order.order_code == codigo.strip().upper(),
                )
                .options(selectinload(Order.items), selectinload(Order.payments))
            )
            order = (await session.execute(stmt)).scalar_one_or_none()
            if not order:
                await interaction.response.send_message("❌ Pedido não encontrado neste servidor.", ephemeral=True)
                return

            is_staff = await has_module_permission(session, interaction.user, BotModule.SALES)
            if order.user_id != interaction.user.id and not is_staff:
                await interaction.response.send_message("🚫 Você só pode consultar seus próprios pedidos.", ephemeral=True)
                return

            items_txt = "\n".join(
                f"• **{i.product_name}** (`x{i.quantity}`) — {'✅ Entregue' if i.is_delivered else '⏳ Pendente'}"
                for i in order.items
            )
            embed = EmbedFactory.build(
                cfg,
                title=f"🧾 Detalhes do Pedido • {order.order_code}",
                description=f"Status: **{order.status.value.upper()}**\n\n**Itens:**\n{items_txt}",
            )
            embed.add_field(name="Cliente", value=f"<@{order.user_id}>", inline=True)
            embed.add_field(
                name="Total",
                value=format_currency(order.total_amount, order.currency),
                inline=True,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="entregar-manual",
        description="[Admin] Realiza a entrega manual de um pedido aprovado ou pendente.",
    )
    @app_commands.describe(codigo="Código do pedido (Ex: FUS-261120-A1B2C3)")
    @app_commands.guild_only()
    async def manual_delivery(self, interaction: discord.Interaction, codigo: str) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.SALES):
                await interaction.response.send_message("🚫 Sem permissão para executar entregas manuais.", ephemeral=True)
                return

            order = (
                await session.execute(
                    select(Order).where(
                        Order.guild_id == interaction.guild.id,
                        Order.order_code == codigo.strip().upper(),
                    )
                )
            ).scalar_one_or_none()

            if not order:
                await interaction.response.send_message("❌ Pedido não encontrado.", ephemeral=True)
                return

            ok, detail = await DeliveryService.deliver_order(
                interaction.client,
                session,
                order.id,
                manual_actor=interaction.user,
            )

        await interaction.response.send_message(
            f"{'✅' if ok else '❌'} {detail}",
            ephemeral=True,
        )

    @app_commands.command(
        name="reembolsar",
        description="[Admin] Processa o reembolso total oficial via API do Mercado Pago.",
    )
    @app_commands.describe(
        codigo="Código do pedido (Ex: FUS-261120-A1B2C3)",
        motivo="Motivo do reembolso para registro nos logs",
    )
    @app_commands.guild_only()
    async def refund_command(
        self,
        interaction: discord.Interaction,
        codigo: str,
        motivo: str = "Reembolso autorizado pela administração",
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        await interaction.response.defer(ephemeral=True)
        async with get_db_session() as session:
            if not await has_module_permission(session, interaction.user, BotModule.MERCADOPAGO):
                await interaction.followup.send("🚫 Sem permissão para autorizar reembolsos.", ephemeral=True)
                return

            ok, msg = await SalesService.refund_order(
                bot=interaction.client,
                session=session,
                guild_id=interaction.guild.id,
                order_code=codigo,
                actor=interaction.user,
                reason=motivo,
            )

        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SalesCog(bot))