"""Folder import: walk the library roots, index video files, probe them with ffprobe."""

import json
import os
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from . import media

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg",
    ".m2ts", ".ts", ".ogv", ".3gp", ".divx", ".vob", ".rmvb", ".asf", ".mts", ".f4v",
}

_JUNK = re.compile(
    r"\b(1080p|2160p|720p|480p|4k|uhd|hdr|x264|x265|h264|h265|hevc|aac|ac3|dts|"
    r"web-?dl|webrip|bluray|brrip|dvdrip|hdtv|xvid|remux|proper|repack)\b",
    re.IGNORECASE,
)
_NOISE = re.compile(r"[._]+")
_BRACKETS = re.compile(r"[\[({][^\])}]*[\])}]")


# Episode numbering, in the shapes that actually turn up in file names.
_SXXEYY = re.compile(r"(?i)\bs(?P<season>\d{1,2})[ ._-]*e(?P<episode>\d{1,3})\b")
_WORDY = re.compile(
    r"(?i)\bseason[ ._-]*(?P<season>\d{1,2})[ ._-]*(?:episode|ep)[ ._-]*(?P<episode>\d{1,3})\b")
_NxNN = re.compile(r"(?i)(?<![\d.])(?P<season>\d{1,2})x(?P<episode>\d{2,3})(?!\d)")
_SEASON_DIR = re.compile(r"(?i)^(?:season|series|s)[ ._-]*(\d{1,2})$")
_EPISODE_ONLY = re.compile(r"(?i)(?:^|[ ._-])(?:e|ep|episode)[ ._-]*(?P<episode>\d{1,3})(?!\d)")
_LEADING_NUMBER = re.compile(r"^\s*(?P<episode>\d{1,3})[ ._-]")


def _series_name(prefix: str, path: Path, root: Path) -> str:
    """Best guess at the show's name: the part before the episode marker,
    else the containing folder (skipping a 'Season 2' folder)."""
    cleaned = clean_title(prefix)
    if len(cleaned) >= 2:
        return cleaned
    folder = path.parent
    if _SEASON_DIR.match(folder.name) and folder.parent != folder and folder != root:
        folder = folder.parent
    if folder == root or not folder.name:
        return ""
    return clean_title(folder.name)


def clean_title(text: str) -> str:
    text = _NOISE.sub(" ", text)
    text = _BRACKETS.sub(" ", text)
    text = _JUNK.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -_.")


def parse_episode(path: Path, root: Path) -> tuple[str, int, int, str] | None:
    """(series, season, episode, episode title) if this file names an episode.

    The episode title is whatever follows the numbering — "S02E11 - Home" gives
    "Home" — and falls back to "Episode 11" when the name carries nothing else.
    """
    stem = path.stem
    for pattern in (_SXXEYY, _WORDY, _NxNN):
        match = pattern.search(stem)
        if match:
            episode = int(match.group("episode"))
            return (_series_name(stem[:match.start()], path, root),
                    int(match.group("season")), episode,
                    clean_title(stem[match.end():]) or f"Episode {episode}")
    # An episode number alone is only trustworthy inside a "Season 2" folder.
    season_dir = _SEASON_DIR.match(path.parent.name)
    if season_dir:
        match = _EPISODE_ONLY.search(stem) or _LEADING_NUMBER.match(stem)
        if match:
            episode = int(match.group("episode"))
            return (_series_name("", path, root), int(season_dir.group(1)), episode,
                    clean_title(stem[match.end():]) or f"Episode {episode}")
    return None


def pretty_title(path: Path) -> str:
    """Turn a filename into something readable without mangling deliberate names."""
    name = path.stem
    if _NOISE.search(name) and " " not in name:
        name = _NOISE.sub(" ", name)
    name = _BRACKETS.sub(" ", name)
    name = _JUNK.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" -_")
    return name or path.stem


def probe(path: str) -> tuple[float, int, int]:
    """Return (duration, width, height) via ffprobe. Zeros if it cannot be read."""
    try:
        out = media.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            timeout=30)
        if out is None:                       # nothing ran: we are shutting down
            return 0.0, 0, 0
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0, 0, 0

    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        pass
    width = height = 0
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if not duration:
                try:
                    duration = float(stream.get("duration") or 0)
                except (TypeError, ValueError):
                    pass
            break
    return duration, width, height


class Scanner(QObject):
    """Runs one full library scan on a worker thread."""

    progress = Signal(int, int, str)   # done, total, current label
    finished = Signal(int, int)        # added, removed
    changed = Signal()                 # emitted as new rows land, throttled

    def __init__(self, library, roots: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.library = library
        self.roots = roots
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        roots = self.roots if self.roots is not None else self.library.folders()
        added = removed = 0
        found_new: list[dict] = []

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
                    if Path(filename).suffix.lower() in VIDEO_EXTENSIONS and not filename.startswith("."):
                        files.append(Path(dirpath) / filename)

            total = len(files)
            for index, file_path in enumerate(files, 1):
                if self._cancel:
                    break
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                path_str = str(file_path)
                seen.add(path_str)
                parsed = parse_episode(file_path, root_path)
                series, season, episode, name = parsed or ("", 0, 0, "")
                title = name if parsed else pretty_title(file_path)
                video_id, needs_probe = self.library.upsert_video(
                    path_str, root, title, stat.st_size, stat.st_mtime,
                    series=series, season=season, episode=episode,
                )
                if needs_probe:
                    added += 1
                    found_new.append({"id": video_id, "path": path_str})
                if index % 20 == 0 or index == total:
                    self.progress.emit(index, total, file_path.name)
                    self.changed.emit()
            removed += self.library.mark_missing(root, seen)

        # Second pass: durations and dimensions, which are the slow part.
        pending = found_new or self.library.unprobed()
        total = len(pending)
        for index, item in enumerate(pending, 1):
            if self._cancel:
                break
            duration, width, height = probe(item["path"])
            self.library.set_probe(item["id"], duration, width, height)
            self.progress.emit(index, total, f"Reading {Path(item['path']).name}")
            if index % 5 == 0 or index == total:
                self.changed.emit()

        self.changed.emit()
        self.finished.emit(added, removed)


class ScanController(QObject):
    """Owns a scanner thread so callers never touch QThread directly.

    `worker_cls` decides what gets scanned — videos by default, audiobooks when
    the audiobook library passes its own scanner in.
    """

    started = Signal()
    progress = Signal(int, int, str)
    finished = Signal(int, int)
    changed = Signal()

    def __init__(self, library, parent=None, worker_cls=None):
        super().__init__(parent)
        self.library = library
        self.worker_cls = worker_cls or Scanner
        self._thread: QThread | None = None
        self._scanner = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, roots: list[str] | None = None, payload=None) -> bool:
        if self.busy:
            return False
        self._thread = QThread()
        self._scanner = self.worker_cls(self.library, roots)
        if payload is not None:
            # Workers that take more than a set of roots (the lyrics fetcher
            # accepts an explicit track list) read it off themselves.
            self._scanner.payload = payload
        self._scanner.moveToThread(self._thread)
        self._thread.started.connect(self._scanner.run)
        self._scanner.progress.connect(self.progress)
        self._scanner.changed.connect(self.changed)
        self._scanner.finished.connect(self._on_finished)
        self._thread.start()
        self.started.emit()
        return True

    def cancel(self) -> None:
        """Ask the worker to stop at its next chance, without waiting for it.

        stop() sees the thread out, which is right at shutdown but would hang
        the window for seconds if a button did it. The worker notices the flag
        between files and finishes the usual way.
        """
        if self._scanner is not None and hasattr(self._scanner, "cancel"):
            self._scanner.cancel()

    def _on_finished(self, added: int, removed: int) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
        self._scanner = None
        self.finished.emit(added, removed)

    def stop(self, wait_ms: int = 3000) -> None:
        """Ask the worker to stop, and see it out.

        The wait is not politeness: a QThread destroyed while it is still
        running takes the process down with it. Most workers notice at once,
        because the ffmpeg they were waiting on has already been killed; one
        that is mid-request to a web service needs longer than the default,
        and the window is hidden by then, so nobody is watching it go.
        """
        if self._scanner is not None:
            self._scanner.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)
