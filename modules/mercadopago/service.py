from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx


MP_API_BASE = "https://api.mercadopago.com"


class MercadoPagoAPIError(Exception):
    """Exceção levantada quando a API oficial do Mercado Pago rejeita uma operação."""

    def __init__(self, status_code: int, message: str, payload: Optional[dict[str, Any]] = None) -> None:
        super().__init__(f"Mercado Pago HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}


class MercadoPagoService:
    """Cliente assíncrono oficial para geração de cobranças PIX, consulta ativa e reembolsos."""

    @staticmethod
    async def create_pix_payment(
        *,
        access_token: str,
        idempotency_key: str,
        order_code: str,
        amount: Decimal,
        description: str,
        payer_email: str,
        payer_first_name: str,
        expires_at: datetime,
        notification_url: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "transaction_amount": float(amount),
            "description": description[:250],
            "payment_method_id": "pix",
            "external_reference": order_code,
            "notification_url": notification_url,
            "date_of_expiration": expires_at.strftime("%Y-%m-%dT%H:%M:%S.000-00:00"),
            "payer": {
                "email": payer_email,
                "first_name": payer_first_name[:60] or "Cliente",
            },
            "metadata": metadata,
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{MP_API_BASE}/v1/payments", json=payload, headers=headers)

        data = response.json() if response.content else {}
        if response.status_code not in (200, 201):
            msg = data.get("message") or str(data)
            raise MercadoPagoAPIError(response.status_code, msg, data)

        return data

    @staticmethod
    async def get_payment(access_token: str, mp_payment_id: str) -> dict[str, Any]:
        """Consulta ativa direta na API oficial do Mercado Pago para validar status e valor."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{MP_API_BASE}/v1/payments/{mp_payment_id}", headers=headers)

        data = response.json() if response.content else {}
        if response.status_code != 200:
            msg = data.get("message") or str(data)
            raise MercadoPagoAPIError(response.status_code, msg, data)

        return data

    @staticmethod
    async def refund_payment(
        access_token: str,
        mp_payment_id: str,
        idempotency_key: str,
        amount: Optional[Decimal] = None,
    ) -> dict[str, Any]:
        """Executa reembolso total ou parcial autorizado na API oficial do Mercado Pago."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {}
        if amount is not None:
            body["amount"] = float(amount)

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{MP_API_BASE}/v1/payments/{mp_payment_id}/refunds",
                json=body,
                headers=headers,
            )

        data = response.json() if response.content else {}
        if response.status_code not in (200, 201):
            msg = data.get("message") or str(data)
            raise MercadoPagoAPIError(response.status_code, msg, data)

        return data