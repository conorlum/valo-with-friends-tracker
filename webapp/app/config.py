from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://valorant:valorant@localhost:5432/valorant_igl_tutor"
    session_secret: str = "dev-only-change-me"
    enable_riot_txt: bool = False
    session_cookie_https_only: bool = False

    @field_validator("database_url")
    @classmethod
    def _use_psycopg2_driver(cls, v: str) -> str:
        # Render's `fromDatabase` connection string is a bare "postgresql://",
        # but SQLAlchemy needs the driver specified.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg2://" + v[len("postgresql://"):]
        return v


settings = Settings()
