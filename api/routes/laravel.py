from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.config import get_settings
from core.database import fastapi_db_session
from core.security import constant_time_compare
from database.models.csgo import CsgoMatchHistory, CsgoServer
from database.models.store import Order, OrderItem, PaymentTransaction, Product
from database.models.vip import UserSteamLink, UserVip, VipPlan, VipStatus

router = APIRouter(prefix="/api/v1/laravel", tags=["FUSION Laravel Website API"])


def verify_laravel_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if not constant_time_compare(x_api_key, settings.laravel_api_key):
        raise HTTPException(status_code=401, detail="Unauthorized Laravel API Key")


@router.get("/products", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_products(
    guild_id: int = Query(..., gt=0),
    only_active: bool = Query(default=True),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = select(Product).where(Product.guild_id == guild_id)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    products = (await session.execute(stmt.order_by(Product.sort_order, Product.id))).scalars().all()
    return [
        {
            "id": p.id,
            "guild_id": p.guild_id,
            "name": p.name,
            "description": p.description,
            "image_url": p.image_url,
            "category": p.category,
            "price": float(p.price),
            "stock_enabled": p.stock_enabled,
            "available_stock": p.available_stock,
            "delivery_type": p.delivery_type.value,
            "role_id": str(p.role_id) if p.role_id else None,
            "vip_plan_id": p.vip_plan_id,
            "duration_days": p.duration_days,
            "is_active": p.is_active,
        }
        for p in products
    ]


@router.get("/customers", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_customers(
    guild_id: int = Query(..., gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(
            Order.user_id,
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(Order.total_amount), 0).label("total_spent"),
            func.max(Order.created_at).label("last_order_at"),
        )
        .where(Order.guild_id == guild_id)
        .group_by(Order.user_id)
        .order_by(desc("total_spent"))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    user_ids = [r.user_id for r in rows]

    steam_links: dict[int, UserSteamLink] = {}
    if user_ids:
        links = (
            await session.execute(
                select(UserSteamLink).where(UserSteamLink.discord_user_id.in_(user_ids))
            )
        ).scalars().all()
        steam_links = {lnk.discord_user_id: lnk for lnk in links}

    result: list[dict[str, Any]] = []
    for row in rows:
        lnk = steam_links.get(row.user_id)
        result.append(
            {
                "discord_user_id": str(row.user_id),
                "steam_id64": lnk.steam_id64 if lnk else None,
                "steam_id2": lnk.steam_id2 if lnk else None,
                "total_orders": int(row.total_orders),
                "total_spent": float(row.total_spent),
                "last_order_at": row.last_order_at.isoformat() if row.last_order_at else None,
            }
        )
    return result


@router.get("/orders", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_orders(
    guild_id: int = Query(..., gt=0),
    discord_user_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(Order)
        .where(Order.guild_id == guild_id)
        .options(selectinload(Order.items))
        .order_by(desc(Order.created_at))
        .limit(limit)
        .offset(offset)
    )
    if discord_user_id is not None:
        stmt = stmt.where(Order.user_id == discord_user_id)
    if status:
        stmt = stmt.where(Order.status == status)

    orders = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": o.id,
            "order_code": o.order_code,
            "discord_user_id": str(o.user_id),
            "coupon_code": o.coupon_code,
            "subtotal_amount": float(o.subtotal_amount),
            "discount_amount": float(o.discount_amount),
            "total_amount": float(o.total_amount),
            "currency": o.currency,
            "status": o.status.value,
            "created_at": o.created_at.isoformat(),
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            "items": [
                {
                    "product_id": i.product_id,
                    "product_name": i.product_name,
                    "unit_price": float(i.unit_price),
                    "quantity": i.quantity,
                    "delivery_type": i.delivery_type.value,
                    "is_delivered": i.is_delivered,
                }
                for i in o.items
            ],
        }
        for o in orders
    ]


@router.get("/payments", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_payments(
    guild_id: int = Query(..., gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(PaymentTransaction, Order.order_code, Order.user_id)
        .join(Order, PaymentTransaction.order_id == Order.id)
        .where(PaymentTransaction.guild_id == guild_id)
        .order_by(desc(PaymentTransaction.created_at))
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": tx.id,
            "order_code": order_code,
            "discord_user_id": str(user_id),
            "mp_payment_id": tx.mp_payment_id,
            "payment_method": tx.payment_method,
            "status": tx.status.value,
            "amount": float(tx.amount),
            "currency": tx.currency,
            "verified_at": tx.verified_at.isoformat() if tx.verified_at else None,
            "refunded_amount": float(tx.refunded_amount),
            "created_at": tx.created_at.isoformat(),
        }
        for tx, order_code, user_id in rows
    ]


@router.get("/vips", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_vips(
    guild_id: int = Query(..., gt=0),
    discord_user_id: Optional[int] = Query(default=None),
    only_active: bool = Query(default=True),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(UserVip)
        .where(UserVip.guild_id == guild_id)
        .options(selectinload(UserVip.plan))
        .order_by(desc(UserVip.created_at))
    )
    if discord_user_id is not None:
        stmt = stmt.where(UserVip.user_id == discord_user_id)
    if only_active:
        stmt = stmt.where(UserVip.status == VipStatus.ACTIVE)

    vips = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": uv.id,
            "discord_user_id": str(uv.user_id),
            "plan_id": uv.plan_id,
            "plan_name": uv.plan.name,
            "role_id": str(uv.plan.role_id),
            "csgo_vip_flag": uv.plan.csgo_vip_flag,
            "status": uv.status.value,
            "started_at": uv.started_at.isoformat(),
            "expires_at": uv.expires_at.isoformat() if uv.expires_at else None,
            "is_permanent": uv.expires_at is None,
        }
        for uv in vips
    ]


@router.get("/deliveries", dependencies=[Depends(verify_laravel_key)])
async def laravel_list_deliveries(
    guild_id: int = Query(..., gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(fastapi_db_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(OrderItem, Order.order_code, Order.user_id, Order.delivered_at)
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.guild_id == guild_id, OrderItem.is_delivered.is_(True))
        .order_by(desc(OrderItem.updated_at))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "order_item_id": item.id,
            "order_code": order_code,
            "discord_user_id": str(user_id),
            "product_name": item.product_name,
            "quantity": item.quantity,
            "delivery_type": item.delivery_type.value,
            "delivered_at": delivered_at.isoformat() if delivered_at else None,
        }
        for item, order_code, user_id, delivered_at in rows
    ]


@router.get("/csgo/overview", dependencies=[Depends(verify_laravel_key)])
async def laravel_csgo_overview(
    guild_id: int = Query(..., gt=0),
    session: AsyncSession = Depends(fastapi_db_session),
) -> dict[str, Any]:
    servers = (
        await session.execute(
            select(CsgoServer).where(CsgoServer.guild_id == guild_id, CsgoServer.is_active.is_(True))
        )
    ).scalars().all()
    matches = (
        await session.execute(
            select(CsgoMatchHistory)
            .where(CsgoMatchHistory.guild_id == guild_id)
            .order_by(desc(CsgoMatchHistory.played_at))
            .limit(15)
        )
    ).scalars().all()

    return {
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "server_type": s.server_type,
                "address": f"{s.ip_address}:{s.port}",
                "is_online": s.is_online,
                "is_maintenance": s.is_maintenance,
                "map": s.cached_map,
                "players": s.cached_players,
                "max_players": s.cached_max_players,
                "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            }
            for s in servers
        ],
        "recent_matches": [
            {
                "match_uid": m.match_uid,
                "map_name": m.map_name,
                "score_ct": m.score_ct,
                "score_t": m.score_t,
                "winner_side": m.winner_side,
                "duration_seconds": m.duration_seconds,
                "demo_filename": m.demo_filename,
                "played_at": m.played_at.isoformat(),
            }
            for m in matches
        ],
    }