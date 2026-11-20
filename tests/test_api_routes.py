from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from api.app import create_fastapi_app
from core.config import get_settings
from core.database import fastapi_db_session
from database.models.vip import UserSteamLink, UserVip, VipPlan, VipStatus


@pytest.mark.asyncio
async def test_fastapi_health_and_sourcemod_vip_check(db_session, mock_discord_env) -> None:
    bot, guild, member = mock_discord_env
    settings = get_settings()

    plan = VipPlan(
        guild_id=guild.id,
        name="VIP Diamante",
        description="VIP CS:GO",
        price=Decimal("49.90"),
        duration_days=30,
        role_id=555666777888999000,
        csgo_vip_flag="vip_diamante",
        is_active=True,
    )
    db_session.add(plan)
    await db_session.flush()

    db_session.add(
        UserSteamLink(
            discord_user_id=member.id,
            steam_id64="76561198000000000",
            steam_id2="STEAM_1:0:19867136",
            verified=True,
        )
    )
    db_session.add(
        UserVip(
            guild_id=guild.id,
            user_id=member.id,
            plan_id=plan.id,
            started_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            status=VipStatus.ACTIVE,
        )
    )
    await db_session.flush()

    app = create_fastapi_app(bot)

    async def _override_db():
        yield db_session

    app.dependency_overrides[fastapi_db_session] = _override_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Healthcheck
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        # 2. Bloqueio sem API Key
        unauth = await client.get("/api/v1/csgo/vip-check/76561198000000000")
        assert unauth.status_code == 401

        # 3. Consulta VIP válida pelo SourceMod
        auth_resp = await client.get(
            "/api/v1/csgo/vip-check/STEAM_1:0:19867136",
            headers={"X-API-Key": settings.sourcemod_api_key},
        )
        assert auth_resp.status_code == 200
        body = auth_resp.json()
        assert body["has_vip"] is True
        assert body["active_plans"][0]["csgo_vip_flag"] == "vip_diamante"