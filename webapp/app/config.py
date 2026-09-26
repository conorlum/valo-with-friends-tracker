from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://valorant:valorant@localhost:5432/valorant_igl_tutor"
    session_secret: str = "dev-only-change-me"
    # The same codebase serves two sites: the friends tracker and the public
    # ValoMaths demo (SITE_NAME=ValoMaths, DEMO_MODE=true on its own DB).
    site_name: str = "ValoWithFriendsTracker"
    demo_mode: bool = False
    # Independent of demo_mode: only the service registered with Riot sets it.
    enable_riot_txt: bool = False
    session_cookie_https_only: bool = False
    # Dev-only replay preview (app/routers/replay_preview.py): a local folder holding
    # <match uuid>/bundle.json from scripts/build_replay_bundle.py. Unset (the default) turns the
    # routes off; demo mode keeps them off whatever this says. Stage 2 replaces it.
    replay_preview_dir: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg2_driver(cls, v: str) -> str:
        # Render's `fromDatabase` connection string is a bare "postgresql://",
        # but SQLAlchemy needs the driver specified.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg2://" + v[len("postgresql://"):]
        return v


settings = Settings()
