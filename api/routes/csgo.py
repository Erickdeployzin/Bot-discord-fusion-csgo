from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import fastapi_db_session
from core.security import constant_time_compare
from database.models.csgo import CsgoServer
from modules.csgo.service import CsgoService

router = APIRouter(prefix="/api/v1/csgo", tags=["CS:GO Legacy SourceMod Bridge"])


def verify_sourcemod_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if not constant_time_compare(x_api_key, settings.sourcemod_api_key):
        raise HTTPException(status_code=401, detail="Invalid SourceMod API Key")


class SourceModPlayerStatInput(BaseModel):
    steam_id: str = Field(..., min_length=5, max_length=32)
    nickname: str = Field(..., min_length=1, max_length=100)
    kills: int = Field(default=0, ge=0)
    deaths: int = Field(default=0, ge=0)
    assists: int = Field(default=0, ge=0)
    headshots: int = Field(default=0, ge=0)
    mvps: int = Field(default=0, ge=0)
    won: bool = Field(default=False)


class SourceModMatchSubmitRequest(BaseModel):
    guild_id: int = Field(..., gt=0)
    match_uid: str = Field(..., min_length=3, max_length=64)
    server_id: Optional[int] = Field(default=None)
    map_name: str = Field(..., min_length=2, max_length=80)
    score_ct: int = Field(..., ge=0)
    score_t: int = Field(..., ge=0)
    winner_side: str = Field(..., pattern="^(CT|T|TIE|ct|t|tie)$")
    duration_seconds: int = Field(default=0, ge=0)
    demo_filename: Optional[str] = Field(default=None, max_length=255)
    players: list[SourceModPlayerStatInput] = Field(default_factory=list)


@router.get("/vip-check/{steam_id}", dependencies=[Depends(verify_sourcemod_key)])
async def check_player_vip(
    steam_id: str,
    guild_id: Optional[int] = Query(default=None),
    session: AsyncSession = Depends(fastapi_db_session),
) -> dict[str, Any]:
    """Endpoint consultado pelo plugin SourceMod no OnClientPostAdminCheck para aplicar VIP em tempo real."""
    try:
        return await CsgoService.check_vip_by_steam(session, steam_id, guild_id=guild_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/matches", dependencies=[Depends(verify_sourcemod_key)])
async def submit_match_result(
    payload: SourceModMatchSubmitRequest,
    session: AsyncSession = Depends(fastapi_db_session),
) -> dict[str, Any]:
    """Recebe resultado real de partida MIX/Retake enviada pelo servidor CS:GO Legacy."""
    match = await CsgoService.record_match_from_sourcemod(
        session,
        guild_id=payload.guild_id,
        match_uid=payload.match_uid,
        server_id=payload.server_id,
        map_name=payload.map_name,
        score_ct=payload.score_ct,
        score_t=payload.score_t,
        winner_side=payload.winner_side,
        duration_seconds=payload.duration_seconds,
        demo_filename=payload.demo_filename,
        players=[p.model_dump() for p in payload.players],
    )
    return {
        "status": "recorded",
        "match_id": match.id,
        "match_uid": match.match_uid,
    }


@router.get("/servers", dependencies=[Depends(verify_sourcemod_key)])
async def list_csgo_servers(
    guild_id: Optional[int] = Query(default=None),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = select(CsgoServer).where(CsgoServer.is_active.is_(True))
    if guild_id is not None:
        stmt = stmt.where(CsgoServer.guild_id == guild_id)
    servers = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": s.id,
            "guild_id": s.guild_id,
            "name": s.name,
            "server_type": s.server_type,
            "ip_address": s.ip_address,
            "port": s.port,
            "is_online": s.is_online,
            "is_maintenance": s.is_maintenance,
            "map": s.cached_map,
            "players": s.cached_players,
            "max_players": s.cached_max_players,
            "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        }
        for s in servers
    ]