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
    # Stage 3: the friends-only replay upload (docs/replay-viewer-plan.md, "Upload"). Off unless
    # both are set, and always off in demo mode. The code and the URL live in the Render dashboard
    # only (REPLAY_UPLOAD_CODE is `sync: false`), never in the repo.
    replay_upload_code: str | None = None
    replay_worker_url: str | None = None
    # Decision 10's cap (approved D10), max(80 MB, 2 x the largest observed .vrf): the six
    # competitive files are 60-91 MB (90,517,500 bytes the largest).
    replay_upload_max_bytes: int = 181_035_000
    # Map control for new replays on the replay worker (docs/map-control-worker-plan.md). Off by
    # default; needs REPLAY_WORKER_URL too, and is always off in demo mode.
    replay_control_remote: bool = False

    @field_validator("database_url")
    @classmethod
    def _use_psycopg2_driver(cls, v: str) -> str:
        # Render's `fromDatabase` connection string is a bare "postgresql://",
        # but SQLAlchemy needs the driver specified.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg2://" + v[len("postgresql://"):]
        return v


settings = Settings()
