from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, GuildScopedMixin, TimestampMixin


class CsgoServer(Base, GuildScopedMixin, TimestampMixin):
    """Servidores CS:GO Legacy monitorados via UDP A2S real e SourceMod REST."""

    __tablename__ = "csgo_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    server_type: Mapped[str] = mapped_column(String(32), default="MIX", nullable=False)  # MIX, RETAKE, DM, etc.
    ip_address: Mapped[str] = mapped_column(String(120), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=27015, nullable=False)
    rcon_password_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_maintenance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maintenance_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cache real da última consulta A2S / SourceMod (nunca simulado)
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cached_map: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cached_players: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_max_players: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Embed de status ao vivo no Discord
    status_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class CsgoMatchHistory(Base, GuildScopedMixin, TimestampMixin):
    """Histórico real de partidas MIX/Retake enviadas pelos plugins SourceMod."""

    __tablename__ = "csgo_match_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("csgo_servers.id", ondelete="SET NULL"), nullable=True)
    match_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    map_name: Mapped[str] = mapped_column(String(80), nullable=False)
    score_ct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score_t: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    winner_side: Mapped[str] = mapped_column(String(16), nullable=False)  # CT | T | TIE
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    demo_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    players_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CsgoPlayerStat(Base, GuildScopedMixin, TimestampMixin):
    """Estatísticas competitivas consolidadas por SteamID64."""

    __tablename__ = "csgo_player_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steam_id64: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    discord_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    kills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    headshots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mvps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matches_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elo_points: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)