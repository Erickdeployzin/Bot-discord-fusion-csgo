from decimal import Decimal
from typing import Callable, Awaitable, Optional

import discord
from sqlalchemy import select

from core.database import get_db_session
from core.security import get_cipher
from database.models.automations import AutomatedMessage, AutomationTrigger
from database.models.csgo import CsgoServer
from database.models.guild import GuildConfig, LogCategory
from database.models.store import Coupon, DeliveryType, DigitalStockItem, GuildMercadoPagoConfig, Product
from database.models.tickets import TicketDepartment, TicketPanel
from database.models.vip import VipPlan
from modules.logs.service import LogService
from utils.helpers import normalize_hex, parse_decimal, validate_url


RefreshCallback = Callable[[discord.Interaction], Awaitable[None]]


class GeneralConfigModal(discord.ui.Modal, title="Configurações Gerais - FUSION"):
    brand_name = discord.ui.TextInput(
        label="Nome da Comunidade / Marca",
        max_length=80,
        required=True,
    )
    currency = discord.ui.TextInput(
        label="Moeda (Ex: BRL, USD)",
        max_length=10,
        required=True,
    )
    timezone_str = discord.ui.TextInput(
        label="Fuso Horário (Ex: America/Sao_Paulo)",
        max_length=64,
        required=True,
    )
    rate_limit = discord.ui.TextInput(
        label="Cooldown Anti-Spam (segundos)",
        max_length=3,
        required=True,
    )

    def __init__(self, config: GuildConfig, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success
        self.brand_name.default = config.brand_name
        self.currency.default = config.currency
        self.timezone_str.default = config.timezone
        self.rate_limit.default = str(config.rate_limit_seconds)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            cooldown = max(1, min(60, int(self.rate_limit.value.strip())))
        except ValueError:
            await interaction.response.send_message("❌ Cooldown inválido. Informe um número entre 1 e 60.", ephemeral=True)
            return

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if not cfg:
                cfg = GuildConfig(guild_id=interaction.guild.id, guild_name=interaction.guild.name)
                session.add(cfg)

            cfg.brand_name = self.brand_name.value.strip()
            cfg.currency = self.currency.value.strip().upper()
            cfg.timezone = self.timezone_str.value.strip()
            cfg.rate_limit_seconds = cooldown

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.SETTINGS,
                title="Configurações Gerais Atualizadas",
                description="Os parâmetros globais do servidor foram alterados pelo painel.",
                actor=interaction.user,
                fields=[
                    ("Marca", cfg.brand_name, True),
                    ("Moeda", cfg.currency, True),
                    ("Fuso Horário", cfg.timezone, True),
                    ("Cooldown", f"{cfg.rate_limit_seconds}s", True),
                ],
            )

        await self.on_success(interaction)


class PersonalizationModal(discord.ui.Modal, title="Identidade Visual - FUSION"):
    primary_color = discord.ui.TextInput(
        label="Cor Primária HEX (Ex: #FF4655)",
        max_length=7,
        required=True,
    )
    secondary_color = discord.ui.TextInput(
        label="Cor Secundária HEX (Ex: #1E1F22)",
        max_length=7,
        required=True,
    )
    footer_text = discord.ui.TextInput(
        label="Texto do Rodapé (Footer)",
        max_length=200,
        required=True,
    )
    logo_url = discord.ui.TextInput(
        label="URL da Logo (Thumbnail)",
        required=False,
    )
    banner_url = discord.ui.TextInput(
        label="URL do Banner (Imagem Grande)",
        required=False,
    )

    def __init__(self, config: GuildConfig, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success
        self.primary_color.default = config.primary_color_hex
        self.secondary_color.default = config.secondary_color_hex
        self.footer_text.default = config.footer_text
        self.logo_url.default = config.logo_url or ""
        self.banner_url.default = config.banner_url or ""

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            logo = validate_url(self.logo_url.value)
            banner = validate_url(self.banner_url.value)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        async with get_db_session() as session:
            cfg = await session.get(GuildConfig, interaction.guild.id)
            if cfg:
                cfg.primary_color_hex = normalize_hex(self.primary_color.value, "#FF4655")
                cfg.secondary_color_hex = normalize_hex(self.secondary_color.value, "#1E1F22")
                cfg.footer_text = self.footer_text.value.strip()
                cfg.logo_url = logo
                cfg.banner_url = banner

                await LogService.dispatch(
                    interaction.client,
                    session,
                    interaction.guild.id,
                    LogCategory.SETTINGS,
                    title="Identidade Visual Atualizada",
                    description="As cores, rodapé e imagens globais do servidor foram atualizadas.",
                    actor=interaction.user,
                    fields=[
                        ("Cor Primária", cfg.primary_color_hex, True),
                        ("Cor Secundária", cfg.secondary_color_hex, True),
                    ],
                )

        await self.on_success(interaction)


class MercadoPagoModal(discord.ui.Modal, title="Credenciais Mercado Pago"):
    access_token = discord.ui.TextInput(
        label="Access Token (APP_USR-...)",
        style=discord.TextStyle.short,
        required=True,
    )
    public_key = discord.ui.TextInput(
        label="Public Key (Opcional)",
        style=discord.TextStyle.short,
        required=False,
    )
    webhook_secret = discord.ui.TextInput(
        label="Webhook Secret (Assinatura)",
        style=discord.TextStyle.short,
        required=False,
    )
    pix_expiration = discord.ui.TextInput(
        label="Expiração do PIX (minutos)",
        default="30",
        max_length=4,
        required=True,
    )

    def __init__(self, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            exp_minutes = max(5, min(1440, int(self.pix_expiration.value.strip())))
        except ValueError:
            await interaction.response.send_message("❌ Expiração PIX inválida (use 5 a 1440 minutos).", ephemeral=True)
            return

        cipher = get_cipher()
        enc_token = cipher.encrypt(self.access_token.value.strip())
        enc_pub = cipher.encrypt(self.public_key.value.strip()) if self.public_key.value.strip() else None
        enc_secret = cipher.encrypt(self.webhook_secret.value.strip()) if self.webhook_secret.value.strip() else None

        async with get_db_session() as session:
            mp_cfg = await session.get(GuildMercadoPagoConfig, interaction.guild.id)
            if not mp_cfg:
                mp_cfg = GuildMercadoPagoConfig(
                    guild_id=interaction.guild.id,
                    access_token_encrypted=enc_token,
                )
                session.add(mp_cfg)
            else:
                mp_cfg.access_token_encrypted = enc_token

            if enc_pub is not None:
                mp_cfg.public_key_encrypted = enc_pub
            if enc_secret is not None:
                mp_cfg.webhook_secret_encrypted = enc_secret
            mp_cfg.pix_expiration_minutes = exp_minutes
            mp_cfg.is_active = True

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.ADMIN_ACTIONS,
                title="Credenciais Mercado Pago Atualizadas",
                description="As credenciais da API oficial Mercado Pago foram criptografadas (Fernet AES) e salvas.",
                actor=interaction.user,
                fields=[("Expiração PIX", f"{exp_minutes} min", True)],
            )

        await self.on_success(interaction)


class CreateTicketPanelModal(discord.ui.Modal, title="Novo Painel de Tickets"):
    name = discord.ui.TextInput(label="Nome Interno do Painel", max_length=100, required=True)
    panel_title = discord.ui.TextInput(label="Título do Embed", max_length=200, required=True)
    description = discord.ui.TextInput(
        label="Descrição do Painel",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )
    component_mode = discord.ui.TextInput(
        label="Modo (dropdown ou buttons)",
        default="dropdown",
        max_length=20,
        required=True,
    )
    max_tickets = discord.ui.TextInput(
        label="Limite de Tickets por Usuário",
        default="2",
        max_length=2,
        required=True,
    )

    def __init__(self, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        mode = self.component_mode.value.strip().lower()
        if mode not in {"dropdown", "buttons"}:
            mode = "dropdown"
        try:
            limit = max(1, min(10, int(self.max_tickets.value.strip())))
        except ValueError:
            limit = 2

        async with get_db_session() as session:
            panel = TicketPanel(
                guild_id=interaction.guild.id,
                name=self.name.value.strip(),
                title=self.panel_title.value.strip(),
                description=self.description.value.strip(),
                component_mode=mode,
                max_tickets_per_user=limit,
            )
            session.add(panel)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.TICKETS,
                title="Painel de Tickets Criado",
                description=f"Painel **{panel.name}** (`#{panel.id}`) criado com modo `{mode}`.",
                actor=interaction.user,
            )

        await self.on_success(interaction)


class CreateDepartmentModal(discord.ui.Modal, title="Novo Departamento de Ticket"):
    name = discord.ui.TextInput(label="Nome do Departamento", max_length=80, required=True)
    description = discord.ui.TextInput(label="Descrição Curta", max_length=100, required=False)
    emoji = discord.ui.TextInput(label="Emoji (Ex: 🎫 ou ID)", max_length=64, required=False)
    welcome_title = discord.ui.TextInput(
        label="Título da Mensagem Inicial",
        default="Atendimento FUSION",
        max_length=200,
        required=True,
    )
    welcome_description = discord.ui.TextInput(
        label="Descrição Inicial ({user} = menção)",
        style=discord.TextStyle.paragraph,
        default="Olá {user}, explique sua solicitação detalhadamente. Nossa equipe responderá em breve.",
        max_length=1500,
        required=True,
    )

    def __init__(self, panel_id: int, on_success: RefreshCallback) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with get_db_session() as session:
            dept = TicketDepartment(
                guild_id=interaction.guild.id,
                panel_id=self.panel_id,
                name=self.name.value.strip(),
                description=self.description.value.strip() or None,
                emoji=self.emoji.value.strip() or None,
                welcome_title=self.welcome_title.value.strip(),
                welcome_description=self.welcome_description.value.strip(),
            )
            session.add(dept)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.TICKETS,
                title="Departamento Adicionado",
                description=f"Departamento **{dept.name}** vinculado ao painel `#{self.panel_id}`.",
                actor=interaction.user,
            )

        await self.on_success(interaction)


class CreateProductModal(discord.ui.Modal, title="Cadastrar Novo Produto"):
    name = discord.ui.TextInput(label="Nome do Produto", max_length=120, required=True)
    description = discord.ui.TextInput(
        label="Descrição Detalhada",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )
    price = discord.ui.TextInput(label="Preço (Ex: 29.90)", max_length=15, required=True)
    category = discord.ui.TextInput(
        label="Categoria (Ex: VIPs, Serviços)",
        default="VIPs",
        max_length=60,
        required=True,
    )
    stock = discord.ui.TextInput(
        label="Estoque (-1 para ilimitado)",
        default="-1",
        max_length=8,
        required=True,
    )

    def __init__(self, delivery_type: DeliveryType, on_success: RefreshCallback) -> None:
        super().__init__()
        self.delivery_type = delivery_type
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            unit_price = parse_decimal(self.price.value)
            raw_stock = int(self.stock.value.strip())
        except ValueError as exc:
            await interaction.response.send_message(f"❌ Erro nos valores: {exc}", ephemeral=True)
            return

        stock_enabled = raw_stock >= 0
        stock_qty = max(0, raw_stock)

        async with get_db_session() as session:
            product = Product(
                guild_id=interaction.guild.id,
                name=self.name.value.strip(),
                description=self.description.value.strip(),
                price=unit_price,
                category=self.category.value.strip(),
                stock_enabled=stock_enabled,
                stock_quantity=stock_qty,
                delivery_type=self.delivery_type,
                is_active=True,
            )
            session.add(product)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.PRODUCTS,
                title="Produto Cadastrado",
                description=f"Produto **{product.name}** (`#{product.id}`) criado com entrega `{self.delivery_type.value}`.",
                actor=interaction.user,
                fields=[
                    ("Preço", f"R$ {product.price}", True),
                    ("Categoria", product.category, True),
                    ("Estoque", str(stock_qty) if stock_enabled else "Ilimitado", True),
                ],
            )

        await self.on_success(interaction)


class AddDigitalKeysModal(discord.ui.Modal, title="Adicionar Chaves Digitais"):
    keys_payload = discord.ui.TextInput(
        label="Chaves / Códigos (1 por linha)",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        required=True,
    )

    def __init__(self, product_id: int, on_success: RefreshCallback) -> None:
        super().__init__()
        self.product_id = product_id
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        lines = [line.strip() for line in self.keys_payload.value.splitlines() if line.strip()]
        if not lines:
            await interaction.response.send_message("❌ Nenhuma chave válida informada.", ephemeral=True)
            return

        cipher = get_cipher()
        async with get_db_session() as session:
            product = await session.get(Product, self.product_id)
            if not product or product.guild_id != interaction.guild.id:
                await interaction.response.send_message("❌ Produto não encontrado.", ephemeral=True)
                return

            for raw_key in lines:
                session.add(
                    DigitalStockItem(
                        guild_id=interaction.guild.id,
                        product_id=product.id,
                        content_encrypted=cipher.encrypt(raw_key),
                    )
                )

            product.stock_enabled = True
            product.stock_quantity += len(lines)

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.PRODUCTS,
                title="Estoque Digital Abastecido",
                description=f"Adicionadas **{len(lines)}** chaves criptografadas ao produto **{product.name}**.",
                actor=interaction.user,
            )

        await self.on_success(interaction)


class CreateCouponModal(discord.ui.Modal, title="Criar Cupom Promocional"):
    code = discord.ui.TextInput(label="Código do Cupom (Ex: FUSION10)", max_length=40, required=True)
    discount_type = discord.ui.TextInput(
        label="Tipo (percent ou fixed)",
        default="percent",
        max_length=16,
        required=True,
    )
    discount_value = discord.ui.TextInput(label="Valor do Desconto", max_length=12, required=True)
    min_order_value = discord.ui.TextInput(
        label="Valor Mínimo do Pedido",
        default="0.00",
        max_length=12,
        required=True,
    )
    max_uses = discord.ui.TextInput(
        label="Limite de Usos (vazio = ilimitado)",
        required=False,
        max_length=8,
    )

    def __init__(self, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        dtype = self.discount_type.value.strip().lower()
        if dtype not in {"percent", "fixed"}:
            await interaction.response.send_message("❌ Tipo inválido. Use `percent` ou `fixed`.", ephemeral=True)
            return

        try:
            dval = parse_decimal(self.discount_value.value)
            min_val = parse_decimal(self.min_order_value.value)
            uses_limit = int(self.max_uses.value.strip()) if self.max_uses.value.strip() else None
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        code_clean = self.code.value.strip().upper()
        async with get_db_session() as session:
            existing = (
                await session.execute(
                    select(Coupon).where(Coupon.guild_id == interaction.guild.id, Coupon.code == code_clean)
                )
            ).scalar_one_or_none()
            if existing:
                await interaction.response.send_message("❌ Já existe um cupom com esse código.", ephemeral=True)
                return

            coupon = Coupon(
                guild_id=interaction.guild.id,
                code=code_clean,
                discount_type=dtype,
                discount_value=dval,
                min_order_value=min_val,
                max_uses=uses_limit,
                is_active=True,
            )
            session.add(coupon)

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.SALES,
                title="Cupom Criado",
                description=f"Cupom **{code_clean}** ({dtype}: {dval}) criado.",
                actor=interaction.user,
            )

        await self.on_success(interaction)


class CreateVipPlanModal(discord.ui.Modal, title="Cadastrar Plano VIP"):
    name = discord.ui.TextInput(label="Nome do Plano (Ex: VIP Diamante)", max_length=100, required=True)
    description = discord.ui.TextInput(
        label="Descrição do Plano",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )
    price_and_days = discord.ui.TextInput(
        label="Preço (Ex: 39.90) | Dias (0 = Perm)",
        default="39.90 | 30",
        max_length=32,
        required=True,
    )
    csgo_flag = discord.ui.TextInput(
        label="Flag/Grupo SourceMod CS:GO (Opcional)",
        placeholder="Ex: vip_diamante ou a",
        max_length=64,
        required=False,
    )
    benefits = discord.ui.TextInput(
        label="Benefícios (separados por ;)",
        placeholder="Slot reservado; Skins exclusivas; Tag no chat",
        max_length=500,
        required=False,
    )

    def __init__(self, role_id: int, on_success: RefreshCallback) -> None:
        super().__init__()
        self.role_id = role_id
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        parts = [p.strip() for p in self.price_and_days.value.split("|")]
        if len(parts) != 2:
            await interaction.response.send_message(
                "❌ Formato inválido. Use `PREÇO | DIAS` (Ex: `39.90 | 30` ou `99.90 | 0`).",
                ephemeral=True,
            )
            return

        try:
            price = parse_decimal(parts[0])
            days = int(parts[1])
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        duration_days: Optional[int] = None if days <= 0 else days
        benefits_list = [b.strip() for b in self.benefits.value.split(";") if b.strip()]

        async with get_db_session() as session:
            plan = VipPlan(
                guild_id=interaction.guild.id,
                name=self.name.value.strip(),
                description=self.description.value.strip(),
                price=price,
                duration_days=duration_days,
                role_id=self.role_id,
                csgo_vip_flag=self.csgo_flag.value.strip() or None,
                benefits=benefits_list,
                is_active=True,
            )
            session.add(plan)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.VIPS,
                title="Plano VIP Cadastrado",
                description=f"Plano **{plan.name}** (`#{plan.id}`) vinculado ao cargo <@&{self.role_id}>.",
                actor=interaction.user,
                fields=[
                    ("Preço", f"R$ {plan.price}", True),
                    ("Duração", f"{duration_days} dias" if duration_days else "Permanente", True),
                    ("Flag CS:GO", plan.csgo_vip_flag or "Nenhuma", True),
                ],
            )

        await self.on_success(interaction)


class CreateAutomationModal(discord.ui.Modal, title="Nova Mensagem Automática"):
    name = discord.ui.TextInput(label="Nome da Automação", max_length=100, required=True)
    content_text = discord.ui.TextInput(
        label="Texto Fora do Embed (Menções)",
        placeholder="Ex: {user} bem-vindo ao {server}!",
        max_length=1000,
        required=False,
    )
    embed_title = discord.ui.TextInput(label="Título do Embed", max_length=256, required=False)
    embed_description = discord.ui.TextInput(
        label="Descrição ({user}, {server}, {date})",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )
    schedule_spec = discord.ui.TextInput(
        label="Horário HH:MM ou Intervalo (min)",
        placeholder="Ex: 19:00 ou 60 (deixe vazio para evento)",
        max_length=20,
        required=False,
    )

    def __init__(
        self,
        trigger_type: AutomationTrigger,
        channel_id: Optional[int],
        on_success: RefreshCallback,
    ) -> None:
        super().__init__()
        self.trigger_type = trigger_type
        self.channel_id = channel_id
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        raw_sched = self.schedule_spec.value.strip()
        schedule_time: Optional[str] = None
        interval_minutes: Optional[int] = None

        if ":" in raw_sched:
            schedule_time = raw_sched[:5]
        elif raw_sched.isdigit():
            interval_minutes = max(1, int(raw_sched))

        async with get_db_session() as session:
            auto_msg = AutomatedMessage(
                guild_id=interaction.guild.id,
                name=self.name.value.strip(),
                trigger_type=self.trigger_type,
                channel_id=self.channel_id,
                content_text=self.content_text.value.strip() or None,
                embed_title=self.embed_title.value.strip() or None,
                embed_description=self.embed_description.value.strip(),
                schedule_time=schedule_time,
                interval_minutes=interval_minutes,
                is_active=True,
            )
            session.add(auto_msg)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.SETTINGS,
                title="Mensagem Automática Criada",
                description=f"Automação **{auto_msg.name}** (`#{auto_msg.id}`) criada ({self.trigger_type.value}).",
                actor=interaction.user,
            )

        await self.on_success(interaction)


class EditAutomationVisualModal(discord.ui.Modal, title="Editar Visual da Automação"):
    color_hex = discord.ui.TextInput(label="Cor HEX (Ex: #FF4655)", max_length=7, required=True)
    image_url = discord.ui.TextInput(label="URL da Imagem Principal", required=False)
    thumbnail_url = discord.ui.TextInput(label="URL da Thumbnail", required=False)
    footer_text = discord.ui.TextInput(label="Texto do Rodapé", max_length=200, required=False)
    weekdays_raw = discord.ui.TextInput(
        label="Dias Semana (0=Seg..6=Dom, ex: 0,2,4)",
        max_length=20,
        required=False,
    )

    def __init__(self, auto_msg: AutomatedMessage, on_success: RefreshCallback) -> None:
        super().__init__()
        self.automation_id = auto_msg.id
        self.on_success = on_success
        self.color_hex.default = auto_msg.embed_color_hex
        self.image_url.default = auto_msg.embed_image_url or ""
        self.thumbnail_url.default = auto_msg.embed_thumbnail_url or ""
        self.footer_text.default = auto_msg.embed_footer_text or ""
        self.weekdays_raw.default = ",".join(str(d) for d in (auto_msg.weekdays or []))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            img = validate_url(self.image_url.value)
            thumb = validate_url(self.thumbnail_url.value)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        days: list[int] = []
        for chunk in self.weekdays_raw.value.split(","):
            chunk = chunk.strip()
            if chunk.isdigit() and 0 <= int(chunk) <= 6:
                days.append(int(chunk))

        async with get_db_session() as session:
            auto_msg = await session.get(AutomatedMessage, self.automation_id)
            if auto_msg and auto_msg.guild_id == interaction.guild.id:
                auto_msg.embed_color_hex = normalize_hex(self.color_hex.value, "#FF4655")
                auto_msg.embed_image_url = img
                auto_msg.embed_thumbnail_url = thumb
                auto_msg.embed_footer_text = self.footer_text.value.strip() or None
                auto_msg.weekdays = sorted(set(days))

        await self.on_success(interaction)


class AddCsgoServerModal(discord.ui.Modal, title="Adicionar Servidor CS:GO"):
    name = discord.ui.TextInput(
        label="Nome do Servidor (Ex: FUSION MIX #1)",
        max_length=100,
        required=True,
    )
    server_type = discord.ui.TextInput(
        label="Tipo (MIX, RETAKE, DM, 4FUN)",
        default="MIX",
        max_length=32,
        required=True,
    )
    ip_address = discord.ui.TextInput(
        label="Endereço IP ou Domínio",
        placeholder="Ex: mix1.fusioncsgoservidores.uk",
        max_length=120,
        required=True,
    )
    port = discord.ui.TextInput(
        label="Porta (Padrão: 27015)",
        default="27015",
        max_length=6,
        required=True,
    )
    rcon_password = discord.ui.TextInput(
        label="Senha RCON (Criptografada)",
        required=False,
        max_length=120,
    )

    def __init__(self, on_success: RefreshCallback) -> None:
        super().__init__()
        self.on_success = on_success

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        try:
            port_num = max(1, min(65535, int(self.port.value.strip())))
        except ValueError:
            await interaction.response.send_message("❌ Porta inválida.", ephemeral=True)
            return

        rcon_enc = (
            get_cipher().encrypt(self.rcon_password.value.strip())
            if self.rcon_password.value.strip()
            else None
        )

        async with get_db_session() as session:
            srv = CsgoServer(
                guild_id=interaction.guild.id,
                name=self.name.value.strip(),
                server_type=self.server_type.value.strip().upper(),
                ip_address=self.ip_address.value.strip(),
                port=port_num,
                rcon_password_encrypted=rcon_enc,
                is_active=True,
            )
            session.add(srv)
            await session.flush()

            await LogService.dispatch(
                interaction.client,
                session,
                interaction.guild.id,
                LogCategory.ADMIN_ACTIONS,
                title="Servidor CS:GO Legacy Cadastrado",
                description=f"Servidor **{srv.name}** (`{srv.ip_address}:{srv.port}`) adicionado ao monitoramento.",
                actor=interaction.user,
            )

        await self.on_success(interaction)