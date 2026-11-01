"""Audiobook import: group audio files into books and read their metadata.

Two layouts cover almost everything in the wild:

* a self-contained container (``.m4b``, ``.m4a``) — one file is one book, and
  its chapters live inside it;
* a folder of ``.mp3``/``.flac``/… files — one folder is one book, and each
  file is a chapter.

Both are handled, and a folder may hold a mix of the two.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import media

# Files that hold a whole audiobook on their own, chapters included.
CONTAINER_EXTENSIONS = {".m4b", ".m4a"}
# Files that are usually one chapter of a book kept in a folder.
CHAPTER_EXTENSIONS = {".mp3", ".ogg", ".opus", ".flac", ".wav", ".wma", ".aac", ".mka", ".m4v"}
AUDIO_EXTENSIONS = CONTAINER_EXTENSIONS | CHAPTER_EXTENSIONS

_NUMBER = re.compile(r"(\d+)")
_TRACK_NOISE = re.compile(
    r"^\s*(?:(?:cd|disc|disk|part|pt|track|chapter|ch)[\s._-]*)?\d{1,3}[\s._-]*[-–—.]?\s*",
    re.IGNORECASE,
)


def natural_key(name: str) -> list:
    """Sort key where 'Chapter 2' comes before 'Chapter 10'."""
    return [int(part) if part.isdigit() else part.lower()
            for part in _NUMBER.split(name)]


def clean_name(text: str) -> str:
    text = re.sub(r"[._]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -_")


def track_title(path: Path, tags: dict) -> str:
    """A readable chapter name: the file's own title tag, or its filename."""
    for key in ("title", "TITLE"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    name = clean_name(path.stem)
    stripped = _TRACK_NOISE.sub("", name).strip()
    return stripped or name


def probe(path: str) -> dict:
    """ffprobe one audio file: duration, tags and chapter count."""
    try:
        out = media.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_chapters", path],
            timeout=60)
        if out is None:                       # nothing ran: we are shutting down
            return {"duration": 0.0, "tags": {}, "chapters": 0}
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"duration": 0.0, "tags": {}, "chapters": 0}

    fmt = data.get("format", {})
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    return {"duration": duration, "tags": tags, "chapters": len(data.get("chapters") or [])}


def read_chapters(path: str) -> list[dict]:
    """Embedded chapter marks, as [{title, start, end}] in seconds."""
    try:
        out = media.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", path],
            timeout=60)
        if out is None:
            return []
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    chapters = []
    for index, chapter in enumerate(data.get("chapters") or [], 1):
        try:
            start = float(chapter.get("start_time") or 0)
            end = float(chapter.get("end_time") or 0)
        except (TypeError, ValueError):
            continue
        title = (chapter.get("tags") or {}).get("title") or f"Chapter {index}"
        chapters.append({"title": str(title), "start": start, "end": end})
    return chapters


def group_books(root: Path) -> list[dict]:
    """Walk a library root and return one entry per book.

    Each entry is {"path", "title", "files": [Path, ...]} where `path` is the
    container file for single-file books, or the folder for multi-file books.
    """
    books: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=natural_key)
        here = Path(dirpath)
        containers, chapters = [], []
        for filename in filenames:
            if filename.startswith("."):
                continue
            suffix = Path(filename).suffix.lower()
            if suffix in CONTAINER_EXTENSIONS:
                containers.append(here / filename)
            elif suffix in CHAPTER_EXTENSIONS:
                chapters.append(here / filename)

        # A folder holding exactly one container and nothing else is that book,
        # and the folder name is usually the better title.
        if len(containers) == 1 and not chapters and here != root:
            books.append({"path": str(containers[0]), "title": clean_name(here.name),
                          "files": containers})
            continue
        for container in sorted(containers, key=lambda p: natural_key(p.name)):
            books.append({"path": str(container), "title": clean_name(container.stem),
                          "files": [container]})
        if chapters:
            ordered = sorted(chapters, key=lambda p: natural_key(p.name))
            books.append({"path": str(here), "title": clean_name(here.name) or str(here),
                          "files": ordered})
    return books


def book_stat(files: list[Path]) -> tuple[int, float]:
    """Total size and newest mtime, used to spot a book that changed on disk."""
    size = 0.0
    mtime = 0.0
    for path in files:
        try:
            info = path.stat()
        except OSError:
            continue
        size += info.st_size
        mtime = max(mtime, info.st_mtime)
    return int(size), mtime


def describe(book_id: int, path: str, files: list[str], fallback_title: str) -> dict:
    """Probe every file of a book and build its details and track list."""
    tracks: list[dict] = []
    total = 0.0
    first_tags: dict = {}
    for index, file_path in enumerate(files):
        info = probe(file_path)
        if index == 0:
            first_tags = info["tags"]
        duration = info["duration"]
        tracks.append({
            "idx": index,
            "path": file_path,
            "title": track_title(Path(file_path), info["tags"]),
            "duration": duration,
            "start": total,
        })
        total += duration

    tags = first_tags
    title = (tags.get("album") or tags.get("title") or "").strip()
    if len(files) == 1 and tags.get("title"):
        title = tags["title"].strip()
    author = (tags.get("album_artist") or tags.get("artist") or "").strip()
    narrator = (tags.get("composer") or tags.get("narrator") or "").strip()
    series = (tags.get("series") or tags.get("show") or tags.get("grouping") or "").strip()
    if narrator and narrator == author:
        narrator = ""
    return {
        "id": book_id,
        "title": title or fallback_title,
        "author": author,
        "narrator": narrator,
        "series": series,
        "duration": total,
        "tracks": tracks,
    }


class BookScanner(QObject):
    """One full audiobook scan, run on a worker thread."""

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
        roots = self.roots if self.roots is not None else self.library.folders("audio")
        added = removed = 0
        pending: list[dict] = []

        for root in roots:
            root_path = Path(root)
            if not root_path.is_dir():
                continue
            found = group_books(root_path)
            seen: set[str] = set()
            for index, book in enumerate(found, 1):
                if self._cancel:
                    break
                seen.add(book["path"])
                size, mtime = book_stat(book["files"])
                book_id, needs_probe = self.library.upsert_book(
                    book["path"], root, book["title"], size, mtime, len(book["files"])
                )
                if needs_probe:
                    added += 1
                    pending.append({"id": book_id, "path": book["path"],
                                    "files": [str(f) for f in book["files"]],
                                    "title": book["title"]})
                if index % 10 == 0 or index == len(found):
                    self.progress.emit(index, len(found), book["title"])
                    self.changed.emit()
            removed += self.library.mark_books_missing(root, seen)

        # Reading durations and tags is the slow half, so it runs afterwards.
        if not pending:
            for row in self.library.unprobed_books():
                files = [t["path"] for t in self.library.book_tracks(row["id"])]
                if not files:
                    files = self._files_for(row["path"])
                pending.append({"id": row["id"], "path": row["path"], "files": files,
                                "title": Path(row["path"]).stem})

        for index, item in enumerate(pending, 1):
            if self._cancel:
                break
            details = describe(item["id"], item["path"], item["files"], item["title"])
            self.library.set_book_details(
                item["id"], title=details["title"], author=details["author"],
                narrator=details["narrator"], series=details["series"],
                duration=details["duration"], tracks=details["tracks"],
            )
            self.progress.emit(index, len(pending), f"Reading {details['title']}")
            if index % 3 == 0 or index == len(pending):
                self.changed.emit()

        self.changed.emit()
        self.finished.emit(added, removed)

    @staticmethod
    def _files_for(path: str) -> list[str]:
        target = Path(path)
        if target.is_file():
            return [str(target)]
        if target.is_dir():
            return [str(p) for p in sorted(
                (p for p in target.iterdir()
                 if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS),
                key=lambda p: natural_key(p.name))]
        return []
