import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

import discord


HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def parse_hex_color(hex_str: Optional[str], fallback: int = 0xFF4655) -> discord.Color:
    """Converte string hexadecimal (#FF4655) em discord.Color com fallback seguro."""
    if not hex_str:
        return discord.Color(fallback)
    cleaned = hex_str.strip()
    if not HEX_COLOR_RE.match(cleaned):
        return discord.Color(fallback)
    cleaned = cleaned.lstrip("#")
    return discord.Color(int(cleaned, 16))


def normalize_hex(hex_str: str, default: str = "#FF4655") -> str:
    """Valida e normaliza string hexadecimal para o formato #RRGGBB."""
    cleaned = hex_str.strip()
    if not HEX_COLOR_RE.match(cleaned):
        return default
    return f"#{cleaned.lstrip('#').upper()}"


def format_currency(value: Decimal | float | int, currency: str = "BRL") -> str:
    """Formata valores monetários no padrão brasileiro ou internacional."""
    amount = Decimal(str(value))
    if currency.upper() == "BRL":
        formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}"
    return f"{currency.upper()} {amount:,.2f}"


def parse_decimal(raw: str) -> Decimal:
    """Converte entrada textual (ex: '29,90' ou '29.90') em Decimal positivo."""
    normalized = raw.strip().replace("R$", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Valor numérico inválido.") from exc
    if value < Decimal("0.00"):
        raise ValueError("O valor não pode ser negativo.")
    return value.quantize(Decimal("0.01"))


def validate_url(url: Optional[str]) -> Optional[str]:
    """Valida URLs HTTP/HTTPS para imagens e thumbnails de embeds."""
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise ValueError("A URL deve começar com http:// ou https://")
    return cleaned


def render_template_variables(
    text: Optional[str],
    *,
    guild: Optional[discord.Guild] = None,
    user: Optional[discord.abc.User] = None,
    tz_name: str = "America/Sao_Paulo",
    extra: Optional[dict[str, str]] = None,
) -> str:
    """
    Substitui variáveis dinâmicas suportadas pelo FUSION BOT:
    {user}, {username}, {server}, {member_count}, {date}, {time}
    """
    if not text:
        return ""

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("America/Sao_Paulo")

    now = datetime.now(tz)
    replacements: dict[str, str] = {
        "{user}": user.mention if user else "@usuário",
        "{username}": user.display_name if user else "Usuário",
        "{server}": guild.name if guild else "FUSION",
        "{member_count}": str(guild.member_count or len(guild.members)) if guild else "0",
        "{date}": now.strftime("%d/%m/%Y"),
        "{time}": now.strftime("%H:%M"),
    }

    if extra:
        for key, val in extra.items():
            formatted_key = key if key.startswith("{") else f"{{{key}}}"
            replacements[formatted_key] = str(val)

    rendered = text
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered