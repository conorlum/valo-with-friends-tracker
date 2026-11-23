"""Persisted preferences, including the updater's skip list and check time."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields

from .constants import KEEP_DAYS_DEFAULT, SITE
from .paths import app_dir


@dataclass
class Config:
    keep_days: int = KEEP_DAYS_DEFAULT
    paused: bool = False
    site: str = SITE
    skip_versions: list[str] = field(default_factory=list)
    last_update_check: float = 0.0
    retry_generation: int = 0  # the constants.RETRY_GENERATION this PC has already acted on

    @classmethod
    def load(cls, env=None):
        try:
            data = json.loads((app_dir(env) / "settings.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})
        except (OSError, ValueError):
            return cls()

    def save(self, env=None):
        path = app_dir(env) / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(asdict(self), stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
