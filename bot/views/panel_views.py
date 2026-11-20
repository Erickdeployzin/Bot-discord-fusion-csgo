from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import discord
from sqlalchemy import delete, func, select

from bot.embeds.factory import EmbedFactory
from bot.modals.panel_modals import (
    AddCsgoServerModal,
    AddDigitalKeysModal,
    CreateAutomationModal,
    CreateCouponModal,
    CreateDepartmentModal,
    CreateProductModal,
    CreateTicketPanelModal,
    CreateVipPlanModal,
    EditAutomationVisualModal,
    GeneralConfigModal,
    MercadoPagoModal,
    PersonalizationModal,
)
from core.database import get_db_session
from core.permissions import has_module_permission
from core.security import get_cipher
from database.models.automations import AutomatedMessage, AutomationTrigger
from database.models.csgo import CsgoServer
from database.models.guild import BotModule, GuildConfig, GuildLogChannel, GuildModulePermission, LogCategory
from database.models.store import (
    Cart,
    Coupon,
    DeliveryType,
    DigitalStockItem,
    GuildMercadoPagoConfig,
    Order,
    OrderStatus,
    Product,
)
from database.models.tickets import Ticket, TicketDepartment, TicketPanel, TicketRating, TicketStatus
from database.models.vip import UserVip, VipPlan, VipStatus
from modules.logs.service import LogService
from utils.helpers import format_currency, render_template_variables


PANEL_CATEGORIES: list[tuple[str, str, str, str]] = [
    ("general", "1. Configurações Gerais", "⚙️", "Marca, moeda, fuso horário, cooldown e manutenção"),
    ("tickets", "2. Tickets", "🎫", "Painéis, departamentos, limites e publicação"),
    ("sales", "3. Vendas", "🛒", "Canal da loja, cupons e histórico de pedidos"),
    ("mercadopago", "4. Mercado Pago", "💳", "Credenciais PIX, webhook secret e teste de API"),
    ("products", "5. Produtos", "📦", "Catálogo, preços, estoque, chaves digitais e cargos"),
    ("carts", "6. Carrinhos", "🧺", "Monitoramento e limpeza de carrinhos ativos"),
    ("vips", "7. VIPs", "👑", "Planos VIP, flags SourceMod, concessão e revogação"),
    ("automations", "8. Mensagens Automáticas", "🤖", "Editor de boas-vindas, anúncios e agendamentos"),
    ("roles", "9. Cargos", "🎭", "Cargos de cliente, auto-role e equipe staff"),
    ("channels", "10. Canais", "📢", "Canal oficial da loja e canais principais"),
    ("logs", "11. Logs", "📋", "Configuração independente dos 11 canais de auditoria"),
    ("csgo", "12. Integração CS:GO", "🔫", "Servidores CS:GO Legacy, manutenção e status A2S"),
    ("statistics", "13. Estatísticas", "📊", "Faturamento, vendas, VIPs, tickets e atendentes"),
    ("security", "14. Segurança", "🛡️", "Permissões por módulo, cargos/usuários e proteção"),
    ("personalization", "15. Personalização", "🎨", "Cores HEX, rodapé, logo e banner da FUSION"),
]


async def ensure_guild_config(guild: discord.Guild) -> GuildConfig:
    """Garante que o servidor possua registro inicial no banco de dados."""
    async with get_db_session() as session:
        cfg = await session.get(GuildConfig, guild.id)
        if not cfg:
            cfg = GuildConfig(guild_id=guild.id, guild_name=guild.name)
            session.add(cfg)
            await session.flush()
        elif cfg.guild_name != guild.name:
            cfg.guild_name = guild.name
        return cfg


class CategorySelect(discord.ui.Select):
    """Seletor principal das 15 categorias do /painel."""

    def __init__(self, current_category: str = "home") -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                emoji=emoji,
                description=desc[:100],
                default=(key == current_category),
            )
            for key, label, emoji, desc in PANEL_CATEGORIES
        ]
        super().__init__(
            placeholder="Navegar entre as 15 categorias do Painel FUSION...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        await render_category_screen(interaction, selected)


class BasePanelView(discord.ui.View):
    """Base segura com verificação de autor e permissão administrativa."""

    def __init__(self, owner_id: int, current_category: str = "home") -> None:
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.current_category = current_category
        self.add_item(CategorySelect(current_category))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "🔒 Apenas o administrador que abriu este `/painel` pode interagir com ele.",
                ephemeral=True,
            )
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        async with get_db_session() as session:
            allowed = await has_module_permission(session, interaction.user, BotModule.ADMIN_PANEL)
        if not allowed:
            await interaction.response.send_message(
                "🚫 Você não possui permissão para administrar o FUSION BOT.",
                ephemeral=True,
            )
            return False
        return True


class AdminHomeView(BasePanelView):
    """Tela inicial do /painel com visão geral e controles rápidos."""

    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "home")

    @discord.ui.button(label="Alternar Manutenção", style=discord.ButtonStyle.secondary, emoji="🚧", row=1)
    async def toggle_maintenance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if cfg:
                cfg.maintenance_mode = not cfg.maintenance_mode
                state = "ATIVADO" if cfg.maintenance_mode else "DESATIVADO"
                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.ADMIN_ACTIONS,
                    title="Modo Manutenção Alterado",
                    description=f"Modo manutenção foi **{state}**.",
                    actor=interaction.user,
                )
        await render_category_screen(interaction, "home")

    @discord.ui.button(label="Atualizar Dados", style=discord.ButtonStyle.primary, emoji="🔄", row=1)
    async def refresh_home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await render_category_screen(interaction, "home")

    @discord.ui.button(label="Fechar Painel", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def close_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="✅ Painel administrativo encerrado.", embed=None, view=None)
        self.stop()


# ==============================================================================
# CATEGORIA 1: CONFIGURAÇÕES GERAIS
# ==============================================================================
class GeneralCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "general")

    @discord.ui.button(label="Editar Configurações", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit_general(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        cfg = await ensure_guild_config(interaction.guild)
        await interaction.response.send_modal(
            GeneralConfigModal(cfg, lambda inter: render_category_screen(inter, "general"))
        )

    @discord.ui.button(label="Alternar Manutenção", style=discord.ButtonStyle.secondary, emoji="🚧", row=1)
    async def toggle_maint(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if cfg:
                cfg.maintenance_mode = not cfg.maintenance_mode
        await render_category_screen(interaction, "general")


# ==============================================================================
# CATEGORIA 2: TICKETS
# ==============================================================================
class TicketPanelSelect(discord.ui.Select):
    def __init__(self, panels: list[TicketPanel], selected_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(
                label=f"#{p.id} - {p.name}"[:100],
                value=str(p.id),
                description=f"Modo: {p.component_mode} | Limite: {p.max_tickets_per_user}/usuário",
                default=(p.id == selected_id),
            )
            for p in panels[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum painel cadastrado", value="none", default=True)]
        super().__init__(placeholder="Selecione um Painel de Tickets...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        val = self.values[0]
        panel_id = int(val) if val.isdigit() else None
        await render_category_screen(interaction, "tickets", extra={"panel_id": panel_id})


class TicketPublishChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, panel_id: Optional[int]) -> None:
        super().__init__(
            placeholder="Selecione o canal para publicar o Painel de Tickets...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=2,
        )
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not self.panel_id:
            await interaction.response.send_message("⚠️ Crie ou selecione um painel primeiro.", ephemeral=True)
            return

        target_channel = self.values[0]
        channel = interaction.guild.get_channel(target_channel.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Canal inválido.", ephemeral=True)
            return

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            panel = await session.get(TicketPanel, self.panel_id)
            if not panel or panel.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Painel não encontrado.", ephemeral=True)
                return

            depts = (
                await session.execute(
                    select(TicketDepartment).where(TicketDepartment.panel_id == panel.id)
                )
            ).scalars().all()

            if not depts:
                await interaction.response.send_message(
                    "⚠️ Adicione pelo menos **1 departamento** ao painel antes de publicá-lo.",
                    ephemeral=True,
                )
                return

            embed = EmbedFactory.build(
                cfg,
                title=panel.title,
                description=panel.description,
                image_url=panel.image_url,
                thumbnail_url=panel.thumbnail_url,
            )

            # Import tardio evita ciclo antes da Etapa 3 ser carregada
            from modules.tickets.views import TicketLauncherView

            view = TicketLauncherView(panel, list(depts))
            msg = await channel.send(embed=embed, view=view)
            panel.channel_id = channel.id
            panel.message_id = msg.id

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.TICKETS,
                title="Painel de Tickets Publicado",
                description=f"Painel **{panel.name}** publicado em {channel.mention}.",
                actor=interaction.user,
            )

        await render_category_screen(interaction, "tickets", extra={"panel_id": self.panel_id})


class TicketsCategoryView(BasePanelView):
    def __init__(self, owner_id: int, panels: list[TicketPanel], selected_panel_id: Optional[int]) -> None:
        super().__init__(owner_id, "tickets")
        self.selected_panel_id = selected_panel_id
        self.add_item(TicketPanelSelect(panels, selected_panel_id))
        self.add_item(TicketPublishChannelSelect(selected_panel_id))

    @discord.ui.button(label="Novo Painel", style=discord.ButtonStyle.success, emoji="➕", row=3)
    async def new_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateTicketPanelModal(lambda inter: render_category_screen(inter, "tickets"))
        )

    @discord.ui.button(label="Adicionar Departamento", style=discord.ButtonStyle.primary, emoji="🗂️", row=3)
    async def add_department(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.selected_panel_id:
            await interaction.response.send_message("⚠️ Selecione um painel de tickets primeiro.", ephemeral=True)
            return
        await interaction.response.send_modal(
            CreateDepartmentModal(
                self.selected_panel_id,
                lambda inter: render_category_screen(inter, "tickets", extra={"panel_id": self.selected_panel_id}),
            )
        )

    @discord.ui.button(label="Ativar/Desativar Painel", style=discord.ButtonStyle.secondary, emoji="⏯️", row=3)
    async def toggle_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_panel_id:
            await interaction.response.send_message("⚠️ Selecione um painel primeiro.", ephemeral=True)
            return
        async with get_db_session() as session:
            panel = await session.get(TicketPanel, self.selected_panel_id)
            if panel and panel.guild_id == interaction.guild.id:
                panel.is_active = not panel.is_active
        await render_category_screen(interaction, "tickets", extra={"panel_id": self.selected_panel_id})


# ==============================================================================
# CATEGORIA 3: VENDAS
# ==============================================================================
class SalesCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "sales")

    @discord.ui.button(label="Criar Cupom", style=discord.ButtonStyle.success, emoji="🎟️", row=1)
    async def create_coupon(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateCouponModal(lambda inter: render_category_screen(inter, "sales"))
        )

    @discord.ui.button(label="Publicar/Atualizar Loja", style=discord.ButtonStyle.primary, emoji="🏪", row=1)
    async def publish_shop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if not cfg or not cfg.shop_channel_id:
                await interaction.response.send_message(
                    "⚠️ Configure primeiro o **Canal da Loja** na categoria **10. Canais**.",
                    ephemeral=True,
                )
                return
            channel = interaction.guild.get_channel(cfg.shop_channel_id)
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message("❌ Canal da loja não encontrado.", ephemeral=True)
                return

            products = (
                await session.execute(
                    select(Product).where(Product.guild_id == interaction.guild.id, Product.is_active.is_(True))
                )
            ).scalars().all()

            from modules.sales.views import StorefrontView

            embed = EmbedFactory.build(
                cfg,
                title=f"🛒 Loja Oficial • {cfg.brand_name}",
                description=(
                    "Selecione os produtos desejados abaixo para adicionar ao seu carrinho individual.\n"
                    "Pagamentos via **PIX Instantâneo (Mercado Pago)** com **entrega automática**."
                ),
                use_guild_banner=True,
            )
            view = StorefrontView(interaction.guild.id, list(products))
            msg = await channel.send(embed=embed, view=view)
            cfg.shop_message_id = msg.id

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.SALES,
                title="Vitrine da Loja Publicada",
                description=f"Loja publicada em {channel.mention} com {len(products)} produtos ativos.",
                actor=interaction.user,
            )

        await interaction.response.send_message("✅ Vitrine da loja publicada com sucesso!", ephemeral=True)


# ==============================================================================
# CATEGORIA 4: MERCADO PAGO
# ==============================================================================
class MercadoPagoCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "mercadopago")

    @discord.ui.button(label="Configurar Credenciais", style=discord.ButtonStyle.primary, emoji="🔑", row=1)
    async def configure_mp(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            MercadoPagoModal(lambda inter: render_category_screen(inter, "mercadopago"))
        )

    @discord.ui.button(label="Alternar Sandbox/Produção", style=discord.ButtonStyle.secondary, emoji="🧪", row=1)
    async def toggle_sandbox(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            mp_cfg = await session.get(GuildMercadoPagoConfig, interaction.guild.id)
            if not mp_cfg:
                await interaction.response.send_message("⚠️ Configure as credenciais primeiro.", ephemeral=True)
                return
            mp_cfg.is_sandbox = not mp_cfg.is_sandbox
        await render_category_screen(interaction, "mercadopago")

    @discord.ui.button(label="Testar Conexão API", style=discord.ButtonStyle.success, emoji="📡", row=1)
    async def test_connection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        import httpx

        async with get_db_session() as session:
            mp_cfg = await session.get(GuildMercadoPagoConfig, interaction.guild.id)
            if not mp_cfg:
                await interaction.response.send_message("⚠️ Nenhuma credencial configurada.", ephemeral=True)
                return
            token = get_cipher().decrypt(mp_cfg.access_token_encrypted)

        if not token:
            await interaction.response.send_message("❌ Falha ao descriptografar token.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.mercadopago.com/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )

        if resp.status_code == 200:
            data = resp.json()
            nickname = data.get("nickname", "Conta MP")
            email = data.get("email", "Oculto")
            await interaction.followup.send(
                f"✅ **Conexão Mercado Pago autenticada com sucesso!**\n• Conta: `{nickname}`\n• E-mail: `{email}`",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"❌ Falha na autenticação Mercado Pago (HTTP `{resp.status_code}`). Verifique o Access Token.",
                ephemeral=True,
            )


# ==============================================================================
# CATEGORIA 5: PRODUTOS
# ==============================================================================
class ProductSelect(discord.ui.Select):
    def __init__(self, products: list[Product], selected_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(
                label=f"#{p.id} - {p.name} ({format_currency(p.price)})"[:100],
                value=str(p.id),
                description=f"Entrega: {p.delivery_type.value} | Ativo: {'Sim' if p.is_active else 'Não'}",
                default=(p.id == selected_id),
            )
            for p in products[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum produto cadastrado", value="none", default=True)]
        super().__init__(placeholder="Selecione um Produto para gerenciar...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        val = self.values[0]
        pid = int(val) if val.isdigit() else None
        await render_category_screen(interaction, "products", extra={"product_id": pid})


class ProductRoleBindSelect(discord.ui.RoleSelect):
    def __init__(self, product_id: Optional[int]) -> None:
        super().__init__(
            placeholder="Vincular Cargo Discord ao Produto selecionado...",
            min_values=1,
            max_values=1,
            row=2,
        )
        self.product_id = product_id

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not self.product_id:
            await interaction.response.send_message("⚠️ Selecione um produto primeiro.", ephemeral=True)
            return
        role = self.values[0]
        async with get_db_session() as session:
            prod = await session.get(Product, self.product_id)
            if prod and prod.guild_id == interaction.guild.id:
                prod.role_id = role.id
                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.PRODUCTS,
                    title="Cargo Vinculado ao Produto",
                    description=f"Produto **{prod.name}** vinculado ao cargo {role.mention}.",
                    actor=interaction.user,
                )
        await render_category_screen(interaction, "products", extra={"product_id": self.product_id})


class ProductsCategoryView(BasePanelView):
    def __init__(self, owner_id: int, products: list[Product], selected_product_id: Optional[int]) -> None:
        super().__init__(owner_id, "products")
        self.selected_product_id = selected_product_id
        self.add_item(ProductSelect(products, selected_product_id))
        self.add_item(ProductRoleBindSelect(selected_product_id))

    @discord.ui.button(label="Novo Produto (Cargo)", style=discord.ButtonStyle.success, emoji="🎭", row=3)
    async def new_role_product(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateProductModal(DeliveryType.DISCORD_ROLE, lambda inter: render_category_screen(inter, "products"))
        )

    @discord.ui.button(label="Novo Produto (Plano VIP)", style=discord.ButtonStyle.success, emoji="👑", row=3)
    async def new_vip_product(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateProductModal(DeliveryType.VIP_PLAN, lambda inter: render_category_screen(inter, "products"))
        )

    @discord.ui.button(label="Novo Produto (Chave Digital)", style=discord.ButtonStyle.success, emoji="🔑", row=3)
    async def new_key_product(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            CreateProductModal(DeliveryType.DIGITAL_KEY, lambda inter: render_category_screen(inter, "products"))
        )

    @discord.ui.button(label="Abastecer Chaves", style=discord.ButtonStyle.primary, emoji="📥", row=4)
    async def add_keys(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.selected_product_id:
            await interaction.response.send_message("⚠️ Selecione um produto primeiro.", ephemeral=True)
            return
        await interaction.response.send_modal(
            AddDigitalKeysModal(
                self.selected_product_id,
                lambda inter: render_category_screen(inter, "products", extra={"product_id": self.selected_product_id}),
            )
        )

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary, emoji="⏯️", row=4)
    async def toggle_product(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_product_id:
            await interaction.response.send_message("⚠️ Selecione um produto primeiro.", ephemeral=True)
            return
        async with get_db_session() as session:
            prod = await session.get(Product, self.selected_product_id)
            if prod and prod.guild_id == interaction.guild.id:
                prod.is_active = not prod.is_active
        await render_category_screen(interaction, "products", extra={"product_id": self.selected_product_id})


# ==============================================================================
# CATEGORIA 6: CARRINHOS
# ==============================================================================
class CartsCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "carts")

    @discord.ui.button(label="Limpar Carrinhos Inativos (>24h)", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def purge_stale_carts(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        async with get_db_session() as session:
            result = await session.execute(
                delete(Cart).where(Cart.guild_id == interaction.guild.id, Cart.updated_at < cutoff)
            )
            removed = result.rowcount or 0
            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.ADMIN_ACTIONS,
                title="Limpeza de Carrinhos",
                description=f"Removidos **{removed}** carrinhos inativos há mais de 24 horas.",
                actor=interaction.user,
            )
        await render_category_screen(interaction, "carts")


# ==============================================================================
# CATEGORIA 7: VIPS
# ==============================================================================
class VipRoleSelect(discord.ui.RoleSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="1º Selecione o Cargo Discord para criar um Plano VIP...",
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await interaction.response.send_modal(
            CreateVipPlanModal(role.id, lambda inter: render_category_screen(inter, "vips"))
        )


class VipGrantUserSelect(discord.ui.UserSelect):
    def __init__(self, plan_id: Optional[int]) -> None:
        super().__init__(
            placeholder="Conceder VIP manual: selecione o membro...",
            min_values=1,
            max_values=1,
            row=2,
        )
        self.plan_id = plan_id

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        if not self.plan_id:
            await interaction.response.send_message("⚠️ Selecione um plano VIP abaixo primeiro.", ephemeral=True)
            return

        target_user = self.values[0]
        async with get_db_session() as session:
            plan = await session.get(VipPlan, self.plan_id)
            if not plan or plan.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Plano VIP não encontrado.", ephemeral=True)
                return

            now = datetime.now(timezone.utc)
            expires_at = (now + timedelta(days=plan.duration_days)) if plan.duration_days else None
            user_vip = UserVip(
                guild_id=interaction.guild.id,
                user_id=target_user.id,
                plan_id=plan.id,
                started_at=now,
                expires_at=expires_at,
                status=VipStatus.ACTIVE,
            )
            session.add(user_vip)

            member = interaction.guild.get_member(target_user.id)
            role = interaction.guild.get_role(plan.role_id)
            if member and role:
                try:
                    await member.add_roles(role, reason=f"VIP concedido manualmente por {interaction.user}")
                except discord.Forbidden:
                    pass

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.VIPS,
                title="VIP Concedido Manualmente",
                description=f"Plano **{plan.name}** concedido para {target_user.mention}.",
                actor=interaction.user,
            )

        await render_category_screen(interaction, "vips", extra={"plan_id": self.plan_id})


class VipPlanSelect(discord.ui.Select):
    def __init__(self, plans: list[VipPlan], selected_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(
                label=f"#{p.id} - {p.name} ({format_currency(p.price)})"[:100],
                value=str(p.id),
                description=f"Duração: {f'{p.duration_days}d' if p.duration_days else 'Permanente'}",
                default=(p.id == selected_id),
            )
            for p in plans[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum plano VIP cadastrado", value="none", default=True)]
        super().__init__(placeholder="Selecione um Plano VIP ativo...", options=options, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        val = self.values[0]
        pid = int(val) if val.isdigit() else None
        await render_category_screen(interaction, "vips", extra={"plan_id": pid})


class VipsCategoryView(BasePanelView):
    def __init__(self, owner_id: int, plans: list[VipPlan], selected_plan_id: Optional[int]) -> None:
        super().__init__(owner_id, "vips")
        self.add_item(VipRoleSelect())
        self.add_item(VipGrantUserSelect(selected_plan_id))
        self.add_item(VipPlanSelect(plans, selected_plan_id))


# ==============================================================================
# CATEGORIA 8: MENSAGENS AUTOMÁTICAS
# ==============================================================================
class AutomationTriggerSelect(discord.ui.Select):
    def __init__(self, channel_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(label="Boas-vindas (Entrada de Membro)", value=AutomationTrigger.MEMBER_JOIN.value, emoji="👋"),
            discord.SelectOption(label="Despedida (Saída de Membro)", value=AutomationTrigger.MEMBER_LEAVE.value, emoji="🚪"),
            discord.SelectOption(label="Periódica por Intervalo (Minutos)", value=AutomationTrigger.PERIODIC_INTERVAL.value, emoji="🔁"),
            discord.SelectOption(label="Programada Semanal (Horário/Dias)", value=AutomationTrigger.WEEKLY_SCHEDULE.value, emoji="📅"),
            discord.SelectOption(label="Anúncio / Regras / Manual", value=AutomationTrigger.MANUAL.value, emoji="📣"),
        ]
        super().__init__(placeholder="Criar nova automação: escolha o gatilho...", options=options, row=1)
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        trigger = AutomationTrigger(self.values[0])
        await interaction.response.send_modal(
            CreateAutomationModal(
                trigger,
                self.channel_id,
                lambda inter: render_category_screen(inter, "automations"),
            )
        )


class AutomationSelect(discord.ui.Select):
    def __init__(self, items: list[AutomatedMessage], selected_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(
                label=f"#{a.id} - {a.name}"[:100],
                value=str(a.id),
                description=f"Gatilho: {a.trigger_type.value} | Ativa: {'Sim' if a.is_active else 'Não'}",
                default=(a.id == selected_id),
            )
            for a in items[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhuma automação criada", value="none", default=True)]
        super().__init__(placeholder="Selecione uma automação existente...", options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        val = self.values[0]
        aid = int(val) if val.isdigit() else None
        await render_category_screen(interaction, "automations", extra={"automation_id": aid})


class AutomationChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, automation_id: Optional[int]) -> None:
        super().__init__(
            placeholder="Definir canal de destino da automação...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
            row=3,
        )
        self.automation_id = automation_id

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        ch = self.values[0]
        if self.automation_id:
            async with get_db_session() as session:
                auto_msg = await session.get(AutomatedMessage, self.automation_id)
                if auto_msg and auto_msg.guild_id == interaction.guild.id:
                    auto_msg.channel_id = ch.id
        await render_category_screen(
            interaction,
            "automations",
            extra={"automation_id": self.automation_id, "channel_id": ch.id},
        )


class AutomationsCategoryView(BasePanelView):
    def __init__(
        self,
        owner_id: int,
        items: list[AutomatedMessage],
        selected_id: Optional[int],
        channel_id: Optional[int] = None,
    ) -> None:
        super().__init__(owner_id, "automations")
        self.selected_id = selected_id
        self.add_item(AutomationTriggerSelect(channel_id))
        self.add_item(AutomationSelect(items, selected_id))
        self.add_item(AutomationChannelSelect(selected_id))

    @discord.ui.button(label="Pré-visualizar", style=discord.ButtonStyle.primary, emoji="👁️", row=4)
    async def preview_automation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_id:
            await interaction.response.send_message("⚠️ Selecione uma automação primeiro.", ephemeral=True)
            return

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            auto_msg = await session.get(AutomatedMessage, self.selected_id)
            if not auto_msg or auto_msg.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Automação não encontrada.", ephemeral=True)
                return

            tz_name = cfg.timezone if cfg else "America/Sao_Paulo"
            content = render_template_variables(
                auto_msg.content_text,
                guild=interaction.guild,
                user=interaction.user,
                tz_name=tz_name,
            )
            title = render_template_variables(
                auto_msg.embed_title,
                guild=interaction.guild,
                user=interaction.user,
                tz_name=tz_name,
            )
            desc = render_template_variables(
                auto_msg.embed_description,
                guild=interaction.guild,
                user=interaction.user,
                tz_name=tz_name,
            )

            embed = EmbedFactory.build(
                cfg,
                title=title or auto_msg.name,
                description=desc,
                image_url=auto_msg.embed_image_url,
                thumbnail_url=auto_msg.embed_thumbnail_url,
            )
            if auto_msg.embed_footer_text:
                embed.set_footer(text=auto_msg.embed_footer_text)

        await interaction.response.send_message(
            content=f"**[PRÉ-VISUALIZAÇÃO]** {content}".strip(),
            embed=embed,
            ephemeral=True,
        )

    @discord.ui.button(label="Editar Visual/Dias", style=discord.ButtonStyle.secondary, emoji="🎨", row=4)
    async def edit_visual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_id:
            await interaction.response.send_message("⚠️ Selecione uma automação primeiro.", ephemeral=True)
            return
        async with get_db_session() as session:
            auto_msg = await session.get(AutomatedMessage, self.selected_id)
            if not auto_msg or auto_msg.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Automação não encontrada.", ephemeral=True)
                return
        await interaction.response.send_modal(
            EditAutomationVisualModal(
                auto_msg,
                lambda inter: render_category_screen(inter, "automations", extra={"automation_id": self.selected_id}),
            )
        )

    @discord.ui.button(label="Enviar Agora", style=discord.ButtonStyle.success, emoji="🚀", row=4)
    async def send_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_id:
            await interaction.response.send_message("⚠️ Selecione uma automação primeiro.", ephemeral=True)
            return

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            auto_msg = await session.get(AutomatedMessage, self.selected_id)
            if not auto_msg or not auto_msg.channel_id:
                await interaction.response.send_message("⚠️ Defina o canal de destino primeiro.", ephemeral=True)
                return

            channel = interaction.guild.get_channel(auto_msg.channel_id)
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message("❌ Canal de destino inválido.", ephemeral=True)
                return

            tz_name = cfg.timezone if cfg else "America/Sao_Paulo"
            content = render_template_variables(auto_msg.content_text, guild=interaction.guild, tz_name=tz_name)
            title = render_template_variables(auto_msg.embed_title, guild=interaction.guild, tz_name=tz_name)
            desc = render_template_variables(auto_msg.embed_description, guild=interaction.guild, tz_name=tz_name)

            embed = EmbedFactory.build(
                cfg,
                title=title or auto_msg.name,
                description=desc,
                image_url=auto_msg.embed_image_url,
                thumbnail_url=auto_msg.embed_thumbnail_url,
            )
            if auto_msg.embed_footer_text:
                embed.set_footer(text=auto_msg.embed_footer_text)

            await channel.send(content=content or None, embed=embed)
            auto_msg.last_sent_at = datetime.now(timezone.utc)

        await interaction.response.send_message("✅ Mensagem enviada no canal configurado!", ephemeral=True)

    @discord.ui.button(label="Ativar/Desativar", style=discord.ButtonStyle.secondary, emoji="⏯️", row=4)
    async def toggle_auto(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_id:
            await interaction.response.send_message("⚠️ Selecione uma automação primeiro.", ephemeral=True)
            return
        async with get_db_session() as session:
            auto_msg = await session.get(AutomatedMessage, self.selected_id)
            if auto_msg and auto_msg.guild_id == interaction.guild.id:
                auto_msg.is_active = not auto_msg.is_active
        await render_category_screen(interaction, "automations", extra={"automation_id": self.selected_id})


# ==============================================================================
# CATEGORIA 9: CARGOS
# ==============================================================================
class GuildRoleConfigSelect(discord.ui.RoleSelect):
    def __init__(self, field_name: str, placeholder: str, row: int) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        role = self.values[0]
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if cfg:
                setattr(cfg, self.field_name, role.id)
                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.SETTINGS,
                    title="Cargo do Sistema Atualizado",
                    description=f"Campo `{self.field_name}` definido para {role.mention}.",
                    actor=interaction.user,
                )
        await render_category_screen(interaction, "roles")


class RolesCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "roles")
        self.add_item(GuildRoleConfigSelect("customer_role_id", "Cargo de Cliente (concedido após compra)...", row=1))
        self.add_item(GuildRoleConfigSelect("auto_role_id", "Auto-Role (concedido ao entrar no servidor)...", row=2))
        self.add_item(GuildRoleConfigSelect("staff_role_id", "Cargo Geral da Equipe / Staff FUSION...", row=3))


# ==============================================================================
# CATEGORIA 10: CANAIS
# ==============================================================================
class ShopChannelConfigSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Selecione o Canal Oficial da Loja...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        ch = self.values[0]
        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if cfg:
                cfg.shop_channel_id = ch.id
                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.SETTINGS,
                    title="Canal da Loja Definido",
                    description=f"Canal oficial da loja alterado para <#{ch.id}>.",
                    actor=interaction.user,
                )
        await render_category_screen(interaction, "channels")


class ChannelsCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "channels")
        self.add_item(ShopChannelConfigSelect())


# ==============================================================================
# CATEGORIA 11: LOGS (11 CANAIS SEPARADOS)
# ==============================================================================
class LogTypeSelect(discord.ui.Select):
    def __init__(self, selected_cat: LogCategory) -> None:
        labels = {
            LogCategory.TICKETS: "Tickets (Abertura/Fechamento)",
            LogCategory.SUPPORT: "Atendimentos (Assumir/Transferir)",
            LogCategory.SALES: "Compras e Pedidos",
            LogCategory.PAYMENTS: "Pagamentos Aprovados",
            LogCategory.REFUNDS: "Reembolsos",
            LogCategory.DELIVERIES: "Entregas Automáticas/Manuais",
            LogCategory.VIPS: "VIPs (Ativação/Expiração)",
            LogCategory.PRODUCTS: "Produtos e Estoque",
            LogCategory.SETTINGS: "Alterações de Configuração",
            LogCategory.ADMIN_ACTIONS: "Ações Administrativas",
            LogCategory.ERRORS: "Erros do Sistema",
        }
        options = [
            discord.SelectOption(
                label=labels[cat],
                value=cat.value,
                default=(cat == selected_cat),
            )
            for cat in LogCategory
        ]
        super().__init__(placeholder="1º Selecione o tipo de log...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = LogCategory(self.values[0])
        await render_category_screen(interaction, "logs", extra={"log_category": chosen})


class LogDestinationChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, log_category: LogCategory) -> None:
        super().__init__(
            placeholder=f"2º Selecione o canal para logs de [{log_category.value}]...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=2,
        )
        self.log_category = log_category

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        ch = self.values[0]
        async with get_db_session() as session:
            stmt = select(GuildLogChannel).where(
                GuildLogChannel.guild_id == interaction.guild.id,
                GuildLogChannel.category == self.log_category,
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if not record:
                record = GuildLogChannel(
                    guild_id=interaction.guild.id,
                    category=self.log_category,
                    channel_id=ch.id,
                    is_enabled=True,
                )
                session.add(record)
            else:
                record.channel_id = ch.id
                record.is_enabled = True

        await render_category_screen(interaction, "logs", extra={"log_category": self.log_category})


class LogsCategoryView(BasePanelView):
    def __init__(self, owner_id: int, selected_log_cat: LogCategory) -> None:
        super().__init__(owner_id, "logs")
        self.selected_log_cat = selected_log_cat
        self.add_item(LogTypeSelect(selected_log_cat))
        self.add_item(LogDestinationChannelSelect(selected_log_cat))

    @discord.ui.button(label="Testar Log Selecionado", style=discord.ButtonStyle.success, emoji="🧪", row=3)
    async def test_log(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            sent = await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                self.selected_log_cat,
                title="Teste de Canal de Log",
                description=f"Verificação operacional do canal de log `{self.selected_log_cat.value}`.",
                actor=interaction.user,
            )
        if sent:
            await interaction.response.send_message("✅ Mensagem de teste enviada com sucesso!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "❌ Canal não configurado ou bot sem permissão de envio.",
                ephemeral=True,
            )


# ==============================================================================
# CATEGORIA 12: INTEGRAÇÃO CS:GO LEGACY
# ==============================================================================
class CsgoServerSelect(discord.ui.Select):
    def __init__(self, servers: list[CsgoServer], selected_id: Optional[int]) -> None:
        options = [
            discord.SelectOption(
                label=f"#{s.id} - {s.name} ({s.ip_address}:{s.port})"[:100],
                value=str(s.id),
                description=f"Tipo: {s.server_type} | Manutenção: {'Sim' if s.is_maintenance else 'Não'}",
                default=(s.id == selected_id),
            )
            for s in servers[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Nenhum servidor CS:GO cadastrado", value="none", default=True)]
        super().__init__(placeholder="Selecione um servidor CS:GO Legacy...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        val = self.values[0]
        sid = int(val) if val.isdigit() else None
        await render_category_screen(interaction, "csgo", extra={"server_id": sid})


class CsgoCategoryView(BasePanelView):
    def __init__(self, owner_id: int, servers: list[CsgoServer], selected_server_id: Optional[int]) -> None:
        super().__init__(owner_id, "csgo")
        self.selected_server_id = selected_server_id
        self.add_item(CsgoServerSelect(servers, selected_server_id))

    @discord.ui.button(label="Adicionar Servidor", style=discord.ButtonStyle.success, emoji="➕", row=2)
    async def add_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(
            AddCsgoServerModal(lambda inter: render_category_screen(inter, "csgo"))
        )

    @discord.ui.button(label="Alternar Manutenção", style=discord.ButtonStyle.secondary, emoji="🛠️", row=2)
    async def toggle_server_maint(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_server_id:
            await interaction.response.send_message("⚠️ Selecione um servidor primeiro.", ephemeral=True)
            return
        async with get_db_session() as session:
            srv = await session.get(CsgoServer, self.selected_server_id)
            if srv and srv.guild_id == interaction.guild.id:
                srv.is_maintenance = not srv.is_maintenance
        await render_category_screen(interaction, "csgo", extra={"server_id": self.selected_server_id})

    @discord.ui.button(label="Consultar Status Real (A2S)", style=discord.ButtonStyle.primary, emoji="📡", row=2)
    async def query_a2s_now(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        if not self.selected_server_id:
            await interaction.response.send_message("⚠️ Selecione um servidor primeiro.", ephemeral=True)
            return

        import a2s

        async with get_db_session() as session:
            srv = await session.get(CsgoServer, self.selected_server_id)
            if not srv or srv.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Servidor não encontrado.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True)
            try:
                info = await a2s.ainfo((srv.ip_address, srv.port), timeout=3.5)
                srv.is_online = True
                srv.cached_map = info.map_name
                srv.cached_players = info.player_count
                srv.cached_max_players = info.max_players
                srv.last_checked_at = datetime.now(timezone.utc)
                msg = (
                    f"🟢 **{srv.name}** ONLINE\n"
                    f"• Mapa atual: `{info.map_name}`\n"
                    f"• Jogadores: `{info.player_count}/{info.max_players}`\n"
                    f"• Conectar: `connect {srv.ip_address}:{srv.port}`"
                )
            except Exception as exc:
                srv.is_online = False
                srv.last_checked_at = datetime.now(timezone.utc)
                msg = f"🔴 **{srv.name}** OFFLINE ou sem resposta UDP A2S (`{exc}`)."

        await interaction.followup.send(msg, ephemeral=True)


# ==============================================================================
# CATEGORIA 13: ESTATÍSTICAS
# ==============================================================================
class StatisticsCategoryView(BasePanelView):
    def __init__(self, owner_id: int, days_filter: int = 30) -> None:
        super().__init__(owner_id, "statistics")
        self.days_filter = days_filter

    @discord.ui.button(label="Hoje (24h)", style=discord.ButtonStyle.secondary, row=1)
    async def filter_1d(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await render_category_screen(interaction, "statistics", extra={"days": 1})

    @discord.ui.button(label="7 Dias", style=discord.ButtonStyle.secondary, row=1)
    async def filter_7d(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await render_category_screen(interaction, "statistics", extra={"days": 7})

    @discord.ui.button(label="30 Dias", style=discord.ButtonStyle.primary, row=1)
    async def filter_30d(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await render_category_screen(interaction, "statistics", extra={"days": 30})

    @discord.ui.button(label="Todo o Período", style=discord.ButtonStyle.secondary, row=1)
    async def filter_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await render_category_screen(interaction, "statistics", extra={"days": 3650})


# ==============================================================================
# CATEGORIA 14: SEGURANÇA
# ==============================================================================
class SecurityModuleSelect(discord.ui.Select):
    def __init__(self, selected_module: BotModule) -> None:
        options = [
            discord.SelectOption(
                label=f"Módulo: {mod.value}",
                value=mod.value,
                default=(mod == selected_module),
            )
            for mod in BotModule
        ]
        super().__init__(placeholder="1º Selecione o módulo para gerenciar permissões...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        mod = BotModule(self.values[0])
        await render_category_screen(interaction, "security", extra={"module": mod})


class SecurityRoleGrantSelect(discord.ui.RoleSelect):
    def __init__(self, module: BotModule) -> None:
        super().__init__(
            placeholder=f"2º Alternar acesso de Cargo ao módulo [{module.value}]...",
            min_values=1,
            max_values=1,
            row=2,
        )
        self.module = module

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        role = self.values[0]
        async with get_db_session() as session:
            stmt = select(GuildModulePermission).where(
                GuildModulePermission.guild_id == interaction.guild.id,
                GuildModulePermission.module == self.module,
                GuildModulePermission.target_type == "role",
                GuildModulePermission.target_id == role.id,
            )
            perm = (await session.execute(stmt)).scalar_one_or_none()
            if perm:
                await session.delete(perm)
                action_txt = "REMOVIDA"
            else:
                session.add(
                    GuildModulePermission(
                        guild_id=interaction.guild.id,
                        module=self.module,
                        target_type="role",
                        target_id=role.id,
                        allowed=True,
                    )
                )
                action_txt = "CONCEDIDA"

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.ADMIN_ACTIONS,
                title="Permissão de Segurança Alterada",
                description=f"Permissão **{action_txt}** para o cargo {role.mention} no módulo `{self.module.value}`.",
                actor=interaction.user,
            )

        await render_category_screen(interaction, "security", extra={"module": self.module})


class SecurityCategoryView(BasePanelView):
    def __init__(self, owner_id: int, selected_module: BotModule) -> None:
        super().__init__(owner_id, "security")
        self.add_item(SecurityModuleSelect(selected_module))
        self.add_item(SecurityRoleGrantSelect(selected_module))


# ==============================================================================
# CATEGORIA 15: PERSONALIZAÇÃO
# ==============================================================================
class PersonalizationCategoryView(BasePanelView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, "personalization")

    @discord.ui.button(label="Editar Cores, Logo e Banner", style=discord.ButtonStyle.primary, emoji="🎨", row=1)
    async def edit_branding(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        assert interaction.guild is not None
        cfg = await ensure_guild_config(interaction.guild)
        await interaction.response.send_modal(
            PersonalizationModal(cfg, lambda inter: render_category_screen(inter, "personalization"))
        )


# ==============================================================================
# ROTEADOR CENTRAL DE RENDERIZAÇÃO DAS 15 TELAS
# ==============================================================================
async def render_category_screen(
    interaction: discord.Interaction,
    category: str,
    *,
    extra: Optional[dict] = None,
    is_initial: bool = False,
) -> None:
    """Monta embed e view dinâmica para qualquer uma das 15 categorias do /painel."""
    assert interaction.guild is not None
    extra = extra or {}
    guild = interaction.guild
    cfg = await ensure_guild_config(guild)

    async with get_db_session() as session:
        cfg = await session.get(GuildConfig, guild.id) or cfg

        if category == "general":
            embed = EmbedFactory.build(
                cfg,
                title="⚙️ 1. Configurações Gerais",
                description="Administre os parâmetros centrais da comunidade diretamente pelo Discord.",
            )
            embed.add_field(name="Marca", value=f"`{cfg.brand_name}`", inline=True)
            embed.add_field(name="Moeda", value=f"`{cfg.currency}`", inline=True)
            embed.add_field(name="Fuso Horário", value=f"`{cfg.timezone}`", inline=True)
            embed.add_field(name="Cooldown Anti-Spam", value=f"`{cfg.rate_limit_seconds}s`", inline=True)
            embed.add_field(
                name="Modo Manutenção",
                value="🚧 **ATIVO**" if cfg.maintenance_mode else "✅ **Desativado**",
                inline=True,
            )
            view: discord.ui.View = GeneralCategoryView(interaction.user.id)

        elif category == "tickets":
            panels = (
                await session.execute(select(TicketPanel).where(TicketPanel.guild_id == guild.id))
            ).scalars().all()
            selected_panel_id = extra.get("panel_id") or (panels[0].id if panels else None)
            depts_text = "Nenhum departamento cadastrado."
            if selected_panel_id:
                depts = (
                    await session.execute(
                        select(TicketDepartment).where(TicketDepartment.panel_id == selected_panel_id)
                    )
                ).scalars().all()
                if depts:
                    depts_text = "\n".join(
                        f"• {d.emoji or '🎫'} **{d.name}** — {d.description or 'Sem descrição'}"
                        for d in depts
                    )

            embed = EmbedFactory.build(
                cfg,
                title="🎫 2. Sistema de Tickets",
                description=(
                    f"Painéis configurados: **{len(panels)}**\n"
                    f"Painel selecionado: `#{selected_panel_id}`\n\n"
                    f"**Departamentos do Painel:**\n{depts_text}"
                ),
            )
            view = TicketCategoryView_safe = TicketsCategoryView(interaction.user.id, list(panels), selected_panel_id)

        elif category == "sales":
            coupons = (
                await session.execute(select(Coupon).where(Coupon.guild_id == guild.id))
            ).scalars().all()
            orders_count = (
                await session.execute(select(func.count(Order.id)).where(Order.guild_id == guild.id))
            ).scalar_one()
            coupons_str = (
                "\n".join(
                    f"• `{c.code}` — {c.discount_value} ({c.discount_type}) | Usos: {c.used_count}/{c.max_uses or '∞'}"
                    for c in coupons[:10]
                )
                or "Nenhum cupom cadastrado."
            )
            embed = EmbedFactory.build(
                cfg,
                title="🛒 3. Sistema de Vendas",
                description=(
                    f"Canal da Loja: {f'<#{cfg.shop_channel_id}>' if cfg.shop_channel_id else '`Não definido`'}\n"
                    f"Total de Pedidos Registrados: **{orders_count}**\n\n"
                    f"**Cupons Promocionais:**\n{coupons_str}"
                ),
            )
            view = SalesCategoryView(interaction.user.id)

        elif category == "mercadopago":
            mp_cfg = await session.get(GuildMercadoPagoConfig, guild.id)
            embed = EmbedFactory.build(
                cfg,
                title="💳 4. Integração Mercado Pago (PIX Oficial)",
                description=(
                    "Credenciais armazenadas com criptografia **Fernet AES** no banco de dados.\n"
                    "Pagamentos são validados via **Webhook assinado + consulta ativa na API oficial**."
                ),
            )
            if mp_cfg:
                embed.add_field(name="Status", value="✅ **Configurado**", inline=True)
                embed.add_field(
                    name="Ambiente",
                    value="🧪 Sandbox" if mp_cfg.is_sandbox else "🟢 Produção",
                    inline=True,
                )
                embed.add_field(name="Expiração PIX", value=f"`{mp_cfg.pix_expiration_minutes} min`", inline=True)
            else:
                embed.add_field(name="Status", value="⚠️ **Aguardando configuração**", inline=False)
            view = MercadoPagoCategoryView(interaction.user.id)

        elif category == "products":
            products = (
                await session.execute(select(Product).where(Product.guild_id == guild.id))
            ).scalars().all()
            selected_prod_id = extra.get("product_id") or (products[0].id if products else None)
            prod_lines = (
                "\n".join(
                    f"• `#{p.id}` **{p.name}** — {format_currency(p.price, cfg.currency)} "
                    f"| Estoque: `{p.available_stock if p.stock_enabled else '∞'}` "
                    f"| Cargo: {f'<@&{p.role_id}>' if p.role_id else '`Nenhum`'}"
                    for p in products[:12]
                )
                or "Nenhum produto cadastrado ainda."
            )
            embed = EmbedFactory.build(
                cfg,
                title="📦 5. Gestão de Produtos",
                description=f"Total de produtos: **{len(products)}**\n\n{prod_lines}",
            )
            view = ProductsCategoryView(interaction.user.id, list(products), selected_prod_id)

        elif category == "carts":
            total_carts = (
                await session.execute(select(func.count(Cart.id)).where(Cart.guild_id == guild.id))
            ).scalar_one()
            embed = EmbedFactory.build(
                cfg,
                title="🧺 6. Carrinhos Individuais",
                description=(
                    f"Carrinhos persistidos ativos neste servidor: **{total_carts}**\n\n"
                    "Os carrinhos são salvos no banco de dados e suportam múltiplos produtos, "
                    "alteração de quantidades, cupons de desconto e reserva transacional de estoque."
                ),
            )
            view = CartsCategoryView(interaction.user.id)

        elif category == "vips":
            plans = (
                await session.execute(select(VipPlan).where(VipPlan.guild_id == guild.id))
            ).scalars().all()
            active_vips_count = (
                await session.execute(
                    select(func.count(UserVip.id)).where(
                        UserVip.guild_id == guild.id,
                        UserVip.status == VipStatus.ACTIVE,
                    )
                )
            ).scalar_one()
            selected_plan_id = extra.get("plan_id") or (plans[0].id if plans else None)
            plans_txt = (
                "\n".join(
                    f"• `#{p.id}` **{p.name}** — {format_currency(p.price, cfg.currency)} "
                    f"({f'{p.duration_days} dias' if p.duration_days else 'Permanente'}) → <@&{p.role_id}>"
                    for p in plans[:12]
                )
                or "Nenhum plano VIP cadastrado."
            )
            embed = EmbedFactory.build(
                cfg,
                title="👑 7. Sistema VIP",
                description=f"Assinaturas VIP ativas: **{active_vips_count}**\n\n**Planos Cadastrados:**\n{plans_txt}",
            )
            view = VipsCategoryView(interaction.user.id, list(plans), selected_plan_id)

        elif category == "automations":
            autos = (
                await session.execute(select(AutomatedMessage).where(AutomatedMessage.guild_id == guild.id))
            ).scalars().all()
            selected_auto_id = extra.get("automation_id") or (autos[0].id if autos else None)
            channel_id = extra.get("channel_id")
            auto_txt = (
                "\n".join(
                    f"• `#{a.id}` **{a.name}** [{a.trigger_type.value}] → "
                    f"{f'<#{a.channel_id}>' if a.channel_id else '`Sem canal`'} "
                    f"({'🟢 Ativa' if a.is_active else '🔴 Pausada'})"
                    for a in autos[:12]
                )
                or "Nenhuma mensagem automática configurada."
            )
            embed = EmbedFactory.build(
                cfg,
                title="🤖 8. Mensagens Automáticas & Agendamentos",
                description=(
                    "Variáveis suportadas: `{user}`, `{username}`, `{server}`, `{member_count}`, `{date}`, `{time}`\n\n"
                    f"**Automações Configuradas:**\n{auto_txt}"
                ),
            )
            view = AutomationsCategoryView(interaction.user.id, list(autos), selected_auto_id, channel_id)

        elif category == "roles":
            embed = EmbedFactory.build(
                cfg,
                title="🎭 9. Configuração de Cargos",
                description="Selecione diretamente os cargos nativos do servidor abaixo.",
            )
            embed.add_field(
                name="Cargo de Cliente",
                value=f"<@&{cfg.customer_role_id}>" if cfg.customer_role_id else "`Não definido`",
                inline=True,
            )
            embed.add_field(
                name="Auto-Role (Entrada)",
                value=f"<@&{cfg.auto_role_id}>" if cfg.auto_role_id else "`Não definido`",
                inline=True,
            )
            embed.add_field(
                name="Cargo Staff Geral",
                value=f"<@&{cfg.staff_role_id}>" if cfg.staff_role_id else "`Não definido`",
                inline=True,
            )
            view = RolesCategoryView(interaction.user.id)

        elif category == "channels":
            embed = EmbedFactory.build(
                cfg,
                title="📢 10. Configuração de Canais",
                description="Defina os canais principais utilizados pelo bot neste servidor.",
            )
            embed.add_field(
                name="Canal Oficial da Loja",
                value=f"<#{cfg.shop_channel_id}>" if cfg.shop_channel_id else "`Não definido`",
                inline=False,
            )
            view = ChannelsCategoryView(interaction.user.id)

        elif category == "logs":
            selected_log_cat: LogCategory = extra.get("log_category", LogCategory.TICKETS)
            log_rows = (
                await session.execute(select(GuildLogChannel).where(GuildLogChannel.guild_id == guild.id))
            ).scalars().all()
            log_map = {row.category: row.channel_id for row in log_rows if row.is_enabled}
            lines = [
                f"• `{cat.value}` → {f'<#{log_map[cat]}>' if cat in log_map else '`Não configurado`'}"
                for cat in LogCategory
            ]
            embed = EmbedFactory.build(
                cfg,
                title="📋 11. Sistema de Logs (11 Canais Independentes)",
                description="\n".join(lines),
            )
            view = LogsCategoryView(interaction.user.id, selected_log_cat)

        elif category == "csgo":
            servers = (
                await session.execute(select(CsgoServer).where(CsgoServer.guild_id == guild.id))
            ).scalars().all()
            selected_srv_id = extra.get("server_id") or (servers[0].id if servers else None)
            srv_lines = (
                "\n".join(
                    f"• `#{s.id}` **{s.name}** (`{s.ip_address}:{s.port}`) — "
                    f"{'🟢 Online' if s.is_online else '🔴 Offline'} | "
                    f"Mapa: `{s.cached_map or 'N/A'}` ({s.cached_players}/{s.cached_max_players})"
                    for s in servers[:12]
                )
                or "Nenhum servidor CS:GO Legacy cadastrado."
            )
            embed = EmbedFactory.build(
                cfg,
                title="🔫 12. Integração CS:GO Legacy & SourceMod",
                description=f"Servidores monitorados via protocolo UDP A2S real:\n\n{srv_lines}",
            )
            view = CsgoCategoryView(interaction.user.id, list(servers), selected_srv_id)

        elif category == "statistics":
            days = int(extra.get("days", 30))
            since = datetime.now(timezone.utc) - timedelta(days=days)

            revenue = (
                await session.execute(
                    select(func.coalesce(func.sum(Order.total_amount), Decimal("0.00"))).where(
                        Order.guild_id == guild.id,
                        Order.status.in_([OrderStatus.PAID, OrderStatus.DELIVERED]),
                        Order.created_at >= since,
                    )
                )
            ).scalar_one()

            approved_orders = (
                await session.execute(
                    select(func.count(Order.id)).where(
                        Order.guild_id == guild.id,
                        Order.status.in_([OrderStatus.PAID, OrderStatus.DELIVERED]),
                        Order.created_at >= since,
                    )
                )
            ).scalar_one()

            pending_orders = (
                await session.execute(
                    select(func.count(Order.id)).where(
                        Order.guild_id == guild.id,
                        Order.status == OrderStatus.PENDING_PAYMENT,
                    )
                )
            ).scalar_one()

            open_tickets = (
                await session.execute(
                    select(func.count(Ticket.id)).where(
                        Ticket.guild_id == guild.id,
                        Ticket.status.in_([TicketStatus.OPEN, TicketStatus.CLAIMED]),
                    )
                )
            ).scalar_one()

            closed_tickets = (
                await session.execute(
                    select(func.count(Ticket.id)).where(
                        Ticket.guild_id == guild.id,
                        Ticket.status == TicketStatus.CLOSED,
                        Ticket.created_at >= since,
                    )
                )
            ).scalar_one()

            avg_rating = (
                await session.execute(
                    select(func.avg(TicketRating.stars)).where(TicketRating.guild_id == guild.id)
                )
            ).scalar_one()

            active_vips = (
                await session.execute(
                    select(func.count(UserVip.id)).where(
                        UserVip.guild_id == guild.id,
                        UserVip.status == VipStatus.ACTIVE,
                    )
                )
            ).scalar_one()

            embed = EmbedFactory.build(
                cfg,
                title=f"📊 13. Dashboard Estatístico (Últimos {days} dias)",
                description="Métricas consolidadas em tempo real do banco de dados da FUSION.",
            )
            embed.add_field(name="Faturamento Aprovado", value=f"**{format_currency(revenue, cfg.currency)}**", inline=True)
            embed.add_field(name="Pedidos Aprovados", value=f"**{approved_orders}**", inline=True)
            embed.add_field(name="Pedidos Pendentes", value=f"**{pending_orders}**", inline=True)
            embed.add_field(name="VIPs Ativos", value=f"**{active_vips}**", inline=True)
            embed.add_field(name="Tickets Abertos", value=f"**{open_tickets}**", inline=True)
            embed.add_field(name="Tickets Fechados", value=f"**{closed_tickets}**", inline=True)
            embed.add_field(
                name="Nota Média Atendimento",
                value=f"⭐ **{round(float(avg_rating), 2)}/5.0**" if avg_rating else "Sem avaliações",
                inline=True,
            )
            view = StatisticsCategoryView(interaction.user.id, days)

        elif category == "security":
            selected_mod: BotModule = extra.get("module", BotModule.ADMIN_PANEL)
            perms = (
                await session.execute(
                    select(GuildModulePermission).where(
                        GuildModulePermission.guild_id == guild.id,
                        GuildModulePermission.module == selected_mod,
                    )
                )
            ).scalars().all()
            perm_lines = (
                "\n".join(
                    f"• {f'<@&{p.target_id}>' if p.target_type == 'role' else f'<@{p.target_id}>'} → "
                    f"{'✅ Permitido' if p.allowed else '🚫 Negado'}"
                    for p in perms
                )
                or "Nenhuma regra extra (apenas Administradores do servidor têm acesso)."
            )
            embed = EmbedFactory.build(
                cfg,
                title="🛡️ 14. Segurança e Controle de Permissões",
                description=(
                    f"Módulo selecionado: **`{selected_mod.value}`**\n"
                    f"Cooldown anti-abuso: **`{cfg.rate_limit_seconds}s`**\n\n"
                    f"**Regras de Acesso do Módulo:**\n{perm_lines}"
                ),
            )
            view = SecurityCategoryView(interaction.user.id, selected_mod)

        elif category == "personalization":
            embed = EmbedFactory.build(
                cfg,
                title="🎨 15. Personalização Visual FUSION",
                description=(
                    "Esta própria mensagem reflete em tempo real as cores, logo, banner e rodapé configurados.\n\n"
                    f"• **Cor Primária:** `{cfg.primary_color_hex}`\n"
                    f"• **Cor Secundária:** `{cfg.secondary_color_hex}`\n"
                    f"• **Rodapé:** `{cfg.footer_text}`\n"
                    f"• **Logo:** `{cfg.logo_url or 'Não definida'}`\n"
                    f"• **Banner:** `{cfg.banner_url or 'Não definido'}`"
                ),
                use_guild_banner=True,
            )
            view = PersonalizationCategoryView(interaction.user.id)

        else:
            embed = EmbedFactory.build(
                cfg,
                title=f"🛡️ Central Administrativa • {cfg.brand_name}",
                description=(
                    "Bem-vindo ao painel de controle completo do **FUSION BOT**.\n"
                    "Utilize o menu seletor abaixo para configurar qualquer um dos **15 módulos** "
                    "diretamente pelo Discord, com persistência imediata no banco de dados."
                ),
                use_guild_banner=True,
            )
            embed.add_field(name="Servidor", value=f"`{guild.name}` (`{guild.id}`)", inline=True)
            embed.add_field(
                name="Status Operacional",
                value="🚧 Manutenção" if cfg.maintenance_mode else "🟢 Operacional",
                inline=True,
            )
            view = AdminHomeView(interaction.user.id)

    if is_initial or not interaction.response.is_done():
        if is_initial:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.edit_original_response(embed=embed, view=view)