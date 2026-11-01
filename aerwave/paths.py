"""XDG locations for the library database, thumbnails and settings."""

import os
from pathlib import Path

APP_DIR_NAME = "aerwave"
# What the app was called before. Its folders hold the library, the settings
# and every cached cover and lyric — everything the app has learnt.
FORMER_DIR_NAME = "aeris"


def _xdg(env_var: str, fallback: str) -> Path:
    """The app's folder under one XDG base, carried over from the old name.

    The first time Aerwave runs where Aeris ran before, the old folder is
    renamed to the new one, whole: the library database, its journal, the
    settings, the caches. A rename on one filesystem moves nothing and loses
    nothing — it is the same files under a new name — and it either happens
    completely or not at all. If it cannot happen (a permissions problem, a
    read-only home), the old folder simply goes on being used, so nothing
    ever starts again from an empty library.
    """
    value = os.environ.get(env_var)
    base = Path(value) if value else Path.home() / fallback
    folder = base / APP_DIR_NAME
    former = base / FORMER_DIR_NAME
    if not folder.exists() and former.is_dir():
        try:
            former.rename(folder)
        except OSError:
            return former
    return folder


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")

THUMB_DIR = CACHE_DIR / "thumbnails"
DB_PATH = DATA_DIR / "library.db"
SCREENSHOT_DIR = Path(os.environ.get("XDG_PICTURES_DIR") or Path.home() / "Pictures") / "Aerwave"


def ensure_dirs() -> None:
    for directory in (DATA_DIR, CACHE_DIR, CONFIG_DIR, THUMB_DIR):
        directory.mkdir(parents=True, exist_ok=True)
