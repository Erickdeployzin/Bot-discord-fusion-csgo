import hashlib
import hmac
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings


class CredentialCipher:
    """Criptografia simétrica Fernet para armazenar tokens e senhas RCON no banco."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("utf-8"))

    def encrypt(self, plain_text: str) -> str:
        if not plain_text:
            return ""
        return self._fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")

    def decrypt(self, cipher_text: str) -> Optional[str]:
        if not cipher_text:
            return None
        try:
            return self._fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return None


def get_cipher() -> CredentialCipher:
    return CredentialCipher(get_settings().encryption_key)


def verify_mercadopago_signature(
    x_signature: Optional[str],
    x_request_id: Optional[str],
    data_id: str,
    webhook_secret: str,
) -> bool:
    """
    Valida assinatura HMAC-SHA256 oficial do Mercado Pago conforme especificação:
    manifest = "id:<data.id>;request-id:<x-request-id>;ts:<ts>;"
    """
    if not x_signature or not x_request_id or not webhook_secret or not data_id:
        return False

    parts: dict[str, str] = {}
    for segment in x_signature.split(","):
        if "=" in segment:
            k, v = segment.strip().split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts")
    v1_hash = parts.get("v1")
    if not ts or not v1_hash:
        return False

    manifest = f"id:{data_id.lower()};request-id:{x_request_id};ts:{ts};"
    expected_hash = hmac.new(
        webhook_secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(expected_hash, v1_hash)


def constant_time_compare(provided: Optional[str], expected: str) -> bool:
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))