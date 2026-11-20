from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais da aplicação carregadas via variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Aplicação
    app_env: str = Field(default="production")
    app_name: str = Field(default="FUSION BOT")
    timezone: str = Field(default="America/Sao_Paulo")

    # Discord
    discord_token: str = Field(..., min_length=20)
    discord_client_id: int = Field(..., gt=0)
    owner_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # Banco de Dados
    database_url: str = Field(..., min_length=10)
    db_pool_size: int = Field(default=15, ge=2, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)

    # Segurança & Criptografia
    encryption_key: str = Field(..., min_length=32)

    # FastAPI / Webhooks
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8080, ge=1, le=65535)
    public_base_url: str = Field(default="http://localhost:8080")

    # Integrações Externas (Laravel & CS:GO SourceMod)
    laravel_api_key: str = Field(..., min_length=16)
    sourcemod_api_key: str = Field(..., min_length=16)

    # Caminhos Locais
    transcripts_dir: Path = Field(default=Path("storage/transcripts"))
    logs_dir: Path = Field(default=Path("storage/logs"))

    @field_validator("owner_ids", mode="before")
    @classmethod
    def parse_owner_ids(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            cleaned = v.strip().strip("[]")
            if not cleaned:
                return []
            return [int(item.strip().strip('"').strip("'")) for item in cleaned.split(",") if item.strip()]
        return []

    @field_validator("public_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    def ensure_directories(self) -> None:
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings