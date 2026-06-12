from functools import lru_cache
from typing import Union

from pydantic import AnyHttpUrl, Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "change-me-in-production"
INSECURE_ADMIN_PASSWORD = "admin12345"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "UTMka+AWG"
    environment: str = "dev"
    api_v1_prefix: str = "/api/v1"

    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://utmka:utmka@localhost:5432/utmka_awg"
    )
    panel_secret_key: str = Field(default=INSECURE_SECRET_KEY)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    admin_email: str = "admin@utmka.app"
    admin_password: str = INSECURE_ADMIN_PASSWORD

    cors_origins: list[Union[AnyHttpUrl, str]] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    default_dns: str = "1.1.1.1"
    default_subnet: str = "10.8.1.0/24"
    default_udp_port_min: int = 1024
    default_udp_port_max: int = 9999

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> "Settings":
        """В production не даём стартовать с небезопасными дефолтами (fail-fast).

        Реальные установки задают секреты в .env (install-panel.sh), поэтому
        проверка их не затрагивает. Срабатывает только если .env неполный/потерян.
        """
        if self.environment.strip().lower() != "production":
            return self

        problems: list[str] = []
        if not self.panel_secret_key or self.panel_secret_key == INSECURE_SECRET_KEY:
            problems.append("PANEL_SECRET_KEY не задан (используется небезопасный дефолт)")
        if "utmka:utmka@" in str(self.database_url):
            problems.append("DATABASE_URL использует дефолтный пароль БД")
        if self.admin_password == INSECURE_ADMIN_PASSWORD:
            problems.append("ADMIN_PASSWORD использует небезопасный дефолт")

        if problems:
            raise RuntimeError(
                "Небезопасная конфигурация в production: "
                + "; ".join(problems)
                + ". Задайте значения в .env (см. .env.example / install-panel.sh)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
