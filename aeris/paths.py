"""XDG locations for the library database, thumbnails and settings."""

import os
from pathlib import Path

APP_DIR_NAME = "aeris"


def _xdg(env_var: str, fallback: str) -> Path:
    value = os.environ.get(env_var)
    base = Path(value) if value else Path.home() / fallback
    return base / APP_DIR_NAME


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")

THUMB_DIR = CACHE_DIR / "thumbnails"
DB_PATH = DATA_DIR / "library.db"
SCREENSHOT_DIR = Path(os.environ.get("XDG_PICTURES_DIR") or Path.home() / "Pictures") / "Aeris"


def ensure_dirs() -> None:
    for directory in (DATA_DIR, CACHE_DIR, CONFIG_DIR, THUMB_DIR):
        directory.mkdir(parents=True, exist_ok=True)
