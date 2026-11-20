from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import fastapi_db_session
from core.logger import logger
from core.security import get_cipher, verify_mercadopago_signature
from database.models.store import GuildMercadoPagoConfig
from modules.sales.service import SalesService

router = APIRouter(prefix="/api/v1/webhooks/mercadopago", tags=["Mercado Pago Webhooks"])


@router.post("/{guild_id}")
async def mercadopago_webhook(
    guild_id: int,
    request: Request,
    data_id_query: Optional[str] = Query(default=None, alias="data.id"),
    id_query: Optional[str] = Query(default=None, alias="id"),
    topic_query: Optional[str] = Query(default=None, alias="type"),
    x_signature: Optional[str] = Header(default=None, alias="x-signature"),
    x_request_id: Optional[str] = Header(default=None, alias="x-request-id"),
    session: AsyncSession = Depends(fastapi_db_session),
) -> dict[str, Any]:
    """
    Recebe notificações assíncronas do Mercado Pago, valida assinatura HMAC oficial
    e aciona a verificação ativa + entrega automática no Discord.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    event_type = (body.get("type") or body.get("topic") or topic_query or "").lower()
    data_obj = body.get("data") if isinstance(body.get("data"), dict) else {}
    raw_payment_id = data_id_query or id_query or data_obj.get("id") or body.get("id")

    if not raw_payment_id:
        return {"status": "ignored", "reason": "missing_data_id"}

    if event_type and "payment" not in event_type:
        return {"status": "ignored", "reason": f"unsupported_event:{event_type}"}

    mp_cfg = await session.get(GuildMercadoPagoConfig, guild_id)
    if not mp_cfg or not mp_cfg.is_active:
        raise HTTPException(status_code=404, detail="Guild Mercado Pago config not found")

    # Validação de assinatura HMAC-SHA256 conforme documentação oficial do Mercado Pago
    if mp_cfg.webhook_secret_encrypted:
        secret = get_cipher().decrypt(mp_cfg.webhook_secret_encrypted)
        if secret:
            is_valid = verify_mercadopago_signature(
                x_signature=x_signature,
                x_request_id=x_request_id,
                data_id=str(raw_payment_id),
                webhook_secret=secret,
            )
            if not is_valid:
                logger.warning(f"[Webhook MP] Assinatura inválida rejeitada para guild_id={guild_id}, id={raw_payment_id}")
                raise HTTPException(status_code=401, detail="Invalid Mercado Pago webhook signature")

    bot = getattr(request.app.state, "bot", None)
    if not bot:
        raise HTTPException(status_code=503, detail="Discord client unavailable")

    ok, detail = await SalesService.verify_and_approve_payment(
        bot=bot,
        session=session,
        guild_id=guild_id,
        mp_payment_id=str(raw_payment_id),
    )
    return {"status": "processed" if ok else "pending_or_rejected", "detail": detail}