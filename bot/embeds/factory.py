from datetime import datetime, timezone
from typing import Optional

import discord

from database.models.guild import GuildConfig
from utils.helpers import parse_hex_color


class EmbedFactory:
    """Constrói embeds padronizadas aplicando a identidade visual configurada por servidor."""

    @staticmethod
    def build(
        config: Optional[GuildConfig],
        *,
        title: str,
        description: Optional[str] = None,
        color_override: Optional[discord.Color] = None,
        image_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        use_guild_banner: bool = False,
        use_guild_logo: bool = True,
    ) -> discord.Embed:
        primary_color = (
            color_override
            if color_override is not None
            else parse_hex_color(config.primary_color_hex if config else "#FF4655")
        )
        footer_text = (
            config.footer_text
            if config and config.footer_text
            else "FUSION • Comunidade CS:GO Servidores"
        )
        logo = config.logo_url if config else None
        banner = config.banner_url if config else None

        embed = discord.Embed(
            title=title,
            description=description,
            color=primary_color,
            timestamp=datetime.now(timezone.utc),
        )

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        elif use_guild_logo and logo:
            embed.set_thumbnail(url=logo)

        if image_url:
            embed.set_image(url=image_url)
        elif use_guild_banner and banner:
            embed.set_image(url=banner)

        embed.set_footer(text=footer_text, icon_url=logo if logo else None)
        return embed

    @staticmethod
    def success(config: Optional[GuildConfig], title: str, description: str) -> discord.Embed:
        return EmbedFactory.build(
            config,
            title=f"✅ {title}",
            description=description,
            color_override=discord.Color.from_rgb(46, 204, 113),
            use_guild_logo=False,
        )

    @staticmethod
    def warning(config: Optional[GuildConfig], title: str, description: str) -> discord.Embed:
        return EmbedFactory.build(
            config,
            title=f"⚠️ {title}",
            description=description,
            color_override=discord.Color.from_rgb(241, 196, 15),
            use_guild_logo=False,
        )

    @staticmethod
    def error(config: Optional[GuildConfig], title: str, description: str) -> discord.Embed:
        return EmbedFactory.build(
            config,
            title=f"❌ {title}",
            description=description,
            color_override=discord.Color.from_rgb(231, 76, 60),
            use_guild_logo=False,
        )