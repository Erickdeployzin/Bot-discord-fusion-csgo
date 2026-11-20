from datetime import datetime, timezone
from typing import Any, Optional

import a2s
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.csgo import CsgoMatchHistory, CsgoPlayerStat, CsgoServer
from database.models.vip import UserSteamLink, UserVip, VipPlan, VipStatus
from modules.vip.service import normalize_steam_ids


class CsgoService:
    """Consulta UDP A2S real, persistência de partidas SourceMod e validação de VIP por SteamID."""

    @staticmethod
    async def query_single_server(server: CsgoServer, timeout: float = 3.0) -> dict[str, Any]:
        """Consulta dados reais do servidor CS:GO Legacy via Source Engine Query (UDP A2S)."""
        now = datetime.now(timezone.utc)
        try:
            info = await a2s.ainfo((server.ip_address, server.port), timeout=timeout)
            server.is_online = True
            server.cached_map = info.map_name
            server.cached_players = int(info.player_count)
            server.cached_max_players = int(info.max_players)
            server.last_checked_at = now

            players_list: list[dict[str, Any]] = []
            try:
                raw_players = await a2s.aplayers((server.ip_address, server.port), timeout=timeout)
                players_list = [
                    {
                        "name": p.name or "Conectando...",
                        "score": int(p.score),
                        "duration_seconds": int(p.duration),
                    }
                    for p in raw_players
                ]
            except Exception:
                players_list = []

            return {
                "id": server.id,
                "name": server.name,
                "server_type": server.server_type,
                "address": f"{server.ip_address}:{server.port}",
                "is_online": True,
                "is_maintenance": server.is_maintenance,
                "map": info.map_name,
                "players": int(info.player_count),
                "max_players": int(info.max_players),
                "player_list": players_list,
                "checked_at": now.isoformat(),
            }
        except Exception as exc:
            server.is_online = False
            server.last_checked_at = now
            return {
                "id": server.id,
                "name": server.name,
                "server_type": server.server_type,
                "address": f"{server.ip_address}:{server.port}",
                "is_online": False,
                "is_maintenance": server.is_maintenance,
                "map": server.cached_map,
                "players": 0,
                "max_players": server.cached_max_players,
                "player_list": [],
                "error": str(exc),
                "checked_at": now.isoformat(),
            }

    @staticmethod
    async def refresh_all_servers(session: AsyncSession) -> int:
        servers = (
            await session.execute(select(CsgoServer).where(CsgoServer.is_active.is_(True)))
        ).scalars().all()
        for srv in servers:
            await CsgoService.query_single_server(srv, timeout=2.5)
        return len(servers)

    @staticmethod
    async def check_vip_by_steam(
        session: AsyncSession,
        raw_steam_id: str,
        guild_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Retorna todas as flags VIP ativas de um jogador CS:GO Legacy para o plugin SourceMod."""
        steam_id64, steam_id2 = normalize_steam_ids(raw_steam_id)

        link = (
            await session.execute(
                select(UserSteamLink).where(UserSteamLink.steam_id64 == steam_id64)
            )
        ).scalar_one_or_none()

        if not link:
            return {
                "has_vip": False,
                "steam_id64": steam_id64,
                "steam_id2": steam_id2,
                "discord_user_id": None,
                "active_plans": [],
            }

        now = datetime.now(timezone.utc)
        stmt = (
            select(UserVip, VipPlan)
            .join(VipPlan, UserVip.plan_id == VipPlan.id)
            .where(
                UserVip.user_id == link.discord_user_id,
                UserVip.status == VipStatus.ACTIVE,
            )
        )
        if guild_id is not None:
            stmt = stmt.where(UserVip.guild_id == guild_id)

        rows = (await session.execute(stmt)).all()
        valid_plans: list[dict[str, Any]] = []
        for uv, plan in rows:
            if uv.expires_at and uv.expires_at <= now:
                continue
            valid_plans.append(
                {
                    "guild_id": uv.guild_id,
                    "plan_id": plan.id,
                    "plan_name": plan.name,
                    "csgo_vip_flag": plan.csgo_vip_flag or "",
                    "expires_at": uv.expires_at.isoformat() if uv.expires_at else None,
                    "is_permanent": uv.expires_at is None,
                }
            )

        return {
            "has_vip": len(valid_plans) > 0,
            "steam_id64": steam_id64,
            "steam_id2": steam_id2,
            "discord_user_id": link.discord_user_id,
            "active_plans": valid_plans,
        }

    @staticmethod
    async def record_match_from_sourcemod(
        session: AsyncSession,
        *,
        guild_id: int,
        match_uid: str,
        server_id: Optional[int],
        map_name: str,
        score_ct: int,
        score_t: int,
        winner_side: str,
        duration_seconds: int,
        demo_filename: Optional[str],
        players: list[dict[str, Any]],
    ) -> CsgoMatchHistory:
        existing = (
            await session.execute(
                select(CsgoMatchHistory).where(CsgoMatchHistory.match_uid == match_uid)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        now = datetime.now(timezone.utc)
        match_record = CsgoMatchHistory(
            guild_id=guild_id,
            server_id=server_id,
            match_uid=match_uid,
            map_name=map_name,
            score_ct=score_ct,
            score_t=score_t,
            winner_side=winner_side.upper(),
            duration_seconds=duration_seconds,
            demo_filename=demo_filename,
            players_snapshot=players,
            played_at=now,
        )
        session.add(match_record)

        for p in players:
            raw_sid = str(p.get("steam_id", "")).strip()
            if not raw_sid:
                continue
            try:
                sid64, _ = normalize_steam_ids(raw_sid)
            except ValueError:
                continue

            link = (
                await session.execute(
                    select(UserSteamLink).where(UserSteamLink.steam_id64 == sid64)
                )
            ).scalar_one_or_none()

            stat = (
                await session.execute(
                    select(CsgoPlayerStat).where(
                        CsgoPlayerStat.guild_id == guild_id,
                        CsgoPlayerStat.steam_id64 == sid64,
                    )
                )
            ).scalar_one_or_none()

            if not stat:
                stat = CsgoPlayerStat(
                    guild_id=guild_id,
                    steam_id64=sid64,
                    discord_user_id=link.discord_user_id if link else None,
                    nickname=str(p.get("nickname", "Jogador"))[:100],
                )
                session.add(stat)
            else:
                stat.nickname = str(p.get("nickname", stat.nickname))[:100]
                if link:
                    stat.discord_user_id = link.discord_user_id

            won = bool(p.get("won", False))
            stat.kills += int(p.get("kills", 0))
            stat.deaths += int(p.get("deaths", 0))
            stat.assists += int(p.get("assists", 0))
            stat.headshots += int(p.get("headshots", 0))
            stat.mvps += int(p.get("mvps", 0))
            stat.matches_played += 1
            if won:
                stat.wins += 1
                stat.elo_points += 15
            else:
                stat.elo_points = max(100, stat.elo_points - 12)

        await session.flush()
        return match_record

    @staticmethod
    async def get_top_players(session: AsyncSession, guild_id: int, limit: int = 10) -> list[CsgoPlayerStat]:
        stmt = (
            select(CsgoPlayerStat)
            .where(CsgoPlayerStat.guild_id == guild_id)
            .order_by(desc(CsgoPlayerStat.elo_points), desc(CsgoPlayerStat.kills))
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())