import hashlib
import hmac

import pytest

from core.security import get_cipher, verify_mercadopago_signature
from modules.vip.service import normalize_steam_ids
from utils.helpers import render_template_variables


def test_fernet_credential_encryption_roundtrip() -> None:
    cipher = get_cipher()
    secret_token = "APP_USR-1234567890-abcdef-fusion-secret"
    encrypted = cipher.encrypt(secret_token)

    assert encrypted != secret_token
    assert cipher.decrypt(encrypted) == secret_token
    assert cipher.decrypt("token_corrompido_invalido") is None


def test_mercadopago_hmac_signature_validation() -> None:
    webhook_secret = "super_secret_mp_key_2026"
    data_id = "9988776655"
    request_id = "req-uuid-1234-5678"
    ts = "1795100000"

    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    valid_v1 = hmac.new(
        webhook_secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    x_signature = f"ts={ts},v1={valid_v1}"

    assert verify_mercadopago_signature(x_signature, request_id, data_id, webhook_secret) is True
    assert verify_mercadopago_signature(x_signature, request_id, "0000000", webhook_secret) is False
    assert verify_mercadopago_signature(x_signature, "wrong-req", data_id, webhook_secret) is False
    assert verify_mercadopago_signature(None, request_id, data_id, webhook_secret) is False


def test_steamid_normalization_all_formats() -> None:
    sid64, sid2 = normalize_steam_ids("76561198000000000")
    assert sid64 == "76561198000000000"
    assert sid2 == "STEAM_1:0:19867136"

    sid64_from_2, sid2_from_2 = normalize_steam_ids("STEAM_1:0:19867136")
    assert sid64_from_2 == "76561198000000000"
    assert sid2_from_2 == "STEAM_1:0:19867136"

    sid64_from_3, _ = normalize_steam_ids("[U:1:39734272]")
    assert sid64_from_3 == "76561198000000000"

    with pytest.raises(ValueError):
        normalize_steam_ids("steam_invalido_123")


def test_template_variables_rendering(mock_discord_env) -> None:
    _, guild, member = mock_discord_env
    template = "Olá {user} ({username}), bem-vindo ao {server}! Somos {member_count} jogadores em {date} às {time}."
    rendered = render_template_variables(template, guild=guild, user=member)

    assert "<@222333444555666777>" in rendered
    assert "Erik" in rendered
    assert "FUSION CS:GO Legacy" in rendered
    assert "350" in rendered
    assert "{user}" not in rendered