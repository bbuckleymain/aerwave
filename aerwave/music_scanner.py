"""Music import: walk the library roots and read each file's tags."""

import json
import os
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import media

MUSIC_EXTENSIONS = {
    ".flac", ".opus", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav", ".wv",
    ".ape", ".aiff", ".aif", ".wma", ".mpc", ".alac",
}

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"

_LEADING_INDEX = re.compile(r"^\s*\d{1,3}[\s._-]+")


def _number(value) -> int:
    """Track and disc tags come as '3', '3/12' or junk."""
    if value is None:
        return 0
    text = str(value).strip()
    if "/" in text:
        text = text.split("/", 1)[0]
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else 0


def _year(tags: dict) -> int:
    for key in ("date", "year", "originalyear", "original_year", "tdrc"):
        match = re.search(r"(\d{4})", str(tags.get(key) or ""))
        if match:
            return int(match.group(1))
    return 0


def read_tags(path: str) -> dict:
    """Everything Aerwave stores about one music file."""
    try:
        out = media.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            timeout=40)
        if out is None:                       # nothing ran: we are shutting down
            return {}
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}

    fmt = data.get("format", {})
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    for key, value in (audio.get("tags") or {}).items():      # some containers tag the stream
        tags.setdefault(str(key).lower(), str(value))

    def number(value, default=0):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    source = Path(path)
    title = (tags.get("title") or "").strip()
    if not title:
        title = _LEADING_INDEX.sub("", source.stem).strip() or source.stem
    artist = (tags.get("artist") or tags.get("performer") or "").strip()
    album_artist = (tags.get("album_artist") or tags.get("albumartist")
                    or tags.get("band") or artist or UNKNOWN_ARTIST).strip()
    album = (tags.get("album") or "").strip() or source.parent.name or UNKNOWN_ALBUM
    try:
        duration = float(fmt.get("duration") or audio.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "title": title,
        "artist": artist or album_artist,
        "album": album,
        "album_artist": album_artist,
        "genre": (tags.get("genre") or "").strip(),
        "year": _year(tags),
        "track_no": _number(tags.get("track") or tags.get("tracknumber")),
        "disc_no": _number(tags.get("disc") or tags.get("discnumber")) or 1,
        "duration": duration,
        "codec": str(audio.get("codec_name") or "").lower(),
        "bitrate": number(fmt.get("bit_rate") or audio.get("bit_rate")) // 1000,
        "samplerate": number(audio.get("sample_rate")),
        "channels": number(audio.get("channels")),
    }


class MusicScanner(QObject):
    """One full music scan, on a worker thread."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)
    changed = Signal()

    def __init__(self, library, roots: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.library = library
        self.roots = roots
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        roots = self.roots if self.roots is not None else self.library.folders("music")
        added = removed = 0
        pending: list[dict] = []

        for root in roots:
            root_path = Path(root)
            if not root_path.is_dir():
                continue
            seen: set[str] = set()
            files: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
                if self._cancel:
                    break
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for filename in sorted(filenames):
                    if (Path(filename).suffix.lower() in MUSIC_EXTENSIONS
                            and not filename.startswith(".")):
                        files.append(Path(dirpath) / filename)

            for index, file_path in enumerate(files, 1):
                if self._cancel:
                    break
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                path_str = str(file_path)
                seen.add(path_str)
                track_id, needs_probe = self.library.upsert_track(
                    path_str, root, stat.st_size, stat.st_mtime)
                if needs_probe:
                    added += 1
                    pending.append({"id": track_id, "path": path_str})
                if index % 40 == 0 or index == len(files):
                    self.progress.emit(index, len(files), file_path.name)
            removed += self.library.mark_tracks_missing(root, seen)

        if not pending:
            pending = self.library.unprobed_tracks()
        for index, item in enumerate(pending, 1):
            if self._cancel:
                break
            self.library.set_track_tags(item["id"], read_tags(item["path"]))
            if index % 15 == 0 or index == len(pending):
                self.progress.emit(index, len(pending), f"Reading tags {index}/{len(pending)}")
                self.changed.emit()

        self.changed.emit()
        self.finished.emit(added, removed)
