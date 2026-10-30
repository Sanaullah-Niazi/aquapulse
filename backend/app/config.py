"""Settings, read from environment variables (Docker Compose passes them from .env).

Secrets have no defaults on purpose: the API refuses to start without them,
instead of silently running with a weak built-in value.
"""

from __future__ import annotations

from functools import cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Database. The API connects as the limited "app" role (FR-28: it cannot
    # UPDATE or DELETE audit entries). Migrations connect as the owner role.
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = Field("aquapulse", alias="POSTGRES_DB")
    db_owner_user: str = Field("aquapulse", alias="POSTGRES_USER")
    db_owner_password: SecretStr = Field(alias="POSTGRES_PASSWORD")
    db_app_user: str = "aquapulse_app"
    db_app_password: SecretStr = Field(alias="APP_DB_PASSWORD")

    # Secret from which the Ed25519 key that signs access tokens is derived.
    # Old secrets stay valid for checking
    # tokens until they expire, so keys can be rotated without logging
    # everyone out (FR-69).
    jwt_secret: SecretStr = Field(alias="JWT_SECRET")
    jwt_previous_secrets: str = Field("", alias="JWT_PREVIOUS_SECRETS")
    jwt_issuer: str = "aquapulse"
    jwt_audience: str = "aquapulse-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    invitation_hours: int = 48
    reset_token_minutes: int = 30

    # Email (Mailpit locally, any SMTP server online).
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mail_from: str = "AquaPulse <no-reply@aquapulse.example.com>"
    # Where links in emails point. The web pages for them arrive in M8.
    public_url: str = Field("http://localhost:8080", alias="PUBLIC_URL")

    mqtt_host: str = Field("mqtt", alias="MQTT_HOST")
    mqtt_port: int = Field(1883, alias="MQTT_PORT")

    # Rate limits (FR-68, FR-72).
    login_max_failures: int = 5
    login_window_s: int = 15 * 60
    api_requests_per_minute: int = 120

    def db_url(self, owner: bool = False, database: str | None = None) -> URL:
        """Connection URL. URL.create escapes special characters in passwords."""
        secret = self.db_owner_password if owner else self.db_app_password
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_owner_user if owner else self.db_app_user,
            password=secret.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=database or self.db_name,
        )


@cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  (values come from the environment)
