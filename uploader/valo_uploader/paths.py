"""Windows paths with injectable environments and no Windows imports."""

import os
from pathlib import Path
from typing import Mapping


def app_dir(env: Mapping[str, str] | None = None) -> Path:
    return Path((os.environ if env is None else env)["APPDATA"]) / "ValoUploader"


def demos_dir(env: Mapping[str, str] | None = None) -> Path:
    return Path((os.environ if env is None else env)["LOCALAPPDATA"]) / "VALORANT" / "Saved" / "Demos"


def install_dir(env: Mapping[str, str] | None = None) -> Path:
    return Path((os.environ if env is None else env)["LOCALAPPDATA"]) / "Programs" / "ValoUploader"
