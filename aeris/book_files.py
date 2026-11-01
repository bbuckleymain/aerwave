"""Putting an audiobook where a tidy shelf would keep it.

Moving a file is the one thing Aeris does that a rescan cannot undo, so this
module is deliberately dull. It never writes over something that is already
there. It never deletes anything it has not first copied and counted. Every
change it makes is handed straight to the caller to be written down, so a whole
run can be put back exactly as it was.

The shelf it builds is::

    <root>/<Author>/<Series>/<nn> - <Title>.m4b
    <root>/<Author>/<Title>.m4b                     (when there is no series)

A book kept as a folder of chapters takes the same place a single file would,
and books in a series share the series folder rather than each getting one of
their own.

What belongs to a book travels with it: an EPUB, a cover, a chapters file — any
file beside it that carries its name, plus the folder's own cover art when the
book is the only thing in there.
"""

import errno
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from .book_scanner import AUDIO_EXTENSIONS

# Files that belong to the book they sit beside, and should follow it.
SIDECAR_SUFFIXES = {".epub", ".jpg", ".jpeg", ".png", ".webp", ".txt", ".cue",
                    ".nfo", ".opf", ".pdf", ".json", ".lrc", ".m3u", ".chp"}

# Characters no sane filesystem should have to carry. Slash and NUL are
# impossible; the rest are merely a bad idea on the external drives and phones
# these libraries tend to live on.
_ILLEGAL = {"/": "-", "\\": "-", ":": " -", "*": "+", "?": "", '"': "'",
            "<": "(", ">": ")", "|": "-", "\0": ""}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
NAME_LIMIT = 110          # bytes to spare on filesystems that stop at 255


class MoveError(Exception):
    """A move that could not be made, with the reason a person would want."""


# --------------------------------------------------------------------- naming

def safe_name(text: str, limit: int = NAME_LIMIT) -> str:
    """A single path component that means the same thing and is safe to write.

    Trailing dots and spaces are taken off because Windows-formatted drives
    silently drop them, which would make the name Aeris wrote and the name on
    the disk disagree — and a tidy that cannot recognise its own output is the
    one that goes wrong.
    """
    text = unicodedata.normalize("NFC", str(text or ""))
    text = _CONTROL.sub("", text)
    for bad, good in _ILLEGAL.items():
        text = text.replace(bad, good)
    text = _SPACES.sub(" ", text).strip(" .")
    while len(text.encode("utf-8", "ignore")) > limit:
        text = text[:-1].rstrip(" .,;-")
    return text or "Untitled"


def shelf_number(number: str) -> str:
    """"4" sorts after "10" as text, so a whole number is written as "04"."""
    number = (number or "").strip()
    if not number:
        return ""
    try:
        value = float(number)
        if not 0 <= value < 10_000:    # a folder name, not a book's place
            return safe_name(number)
        if value == int(value):
            return f"{int(value):02d}"
        whole, _, fraction = f"{value:g}".partition(".")
        return f"{int(whole):02d}.{fraction}"
    except (ValueError, OverflowError):
        return safe_name(number)


def target_for(root: str, source: Path, *, title: str, author: str,
               series: str = "", number: str = "") -> Path:
    """Where this book would live on a tidy shelf."""
    place = Path(root) / safe_name(author or "Unknown Author")
    if series:
        place = place / safe_name(series)
    label = safe_name(title)
    index = shelf_number(number)
    if index:
        label = safe_name(f"{index} - {label}")
    suffix = "" if source.is_dir() else source.suffix.lower()
    return place / f"{label}{suffix}"


# ------------------------------------------------------------------- sidecars

def sidecars(source: Path) -> list[Path]:
    """The files that belong with this book and should move with it."""
    if source.is_dir():
        return []                      # a folder book already carries its own
    folder = source.parent
    try:
        entries = sorted(p for p in folder.iterdir() if p.is_file())
    except OSError:
        return []
    lowered = source.stem.lower()
    alone = not any(entry.suffix.lower() in AUDIO_EXTENSIONS and entry != source
                    for entry in entries)
    found = []
    for entry in entries:
        if entry == source or entry.suffix.lower() not in SIDECAR_SUFFIXES:
            continue
        # A file named after the book is certainly the book's. When the book is
        # the only one in the folder, so is everything else in there — the
        # EPUB called after the title rather than the file, the cover called
        # "cover.jpg", the notes someone left.
        if alone or entry.name.lower().startswith(lowered + "."):
            found.append(entry)
    return found


def _renamed(entry: Path, source: Path, target: Path) -> str:
    """What a sidecar is called once the book it belongs to has been renamed."""
    if entry.name.lower().startswith(source.stem.lower()):
        return target.stem + entry.name[len(source.stem):]
    return target.stem + entry.suffix.lower()


# --------------------------------------------------------------------- moving

def _weigh(path: Path) -> tuple[int, int]:
    """(files, bytes) — enough to tell a finished copy from a half of one."""
    if path.is_file():
        return 1, path.stat().st_size
    count = total = 0
    for base, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                continue
            count += 1
    return count, total


def _discard(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def same_file(one: Path, other: Path) -> bool:
    try:
        return one.exists() and other.exists() and os.path.samefile(one, other)
    except OSError:
        return False


def relocate(source: Path, target: Path) -> None:
    """Move one path to another that is free. Raises MoveError, or succeeds.

    Within a filesystem this is a rename, which either happens or does not.
    Across one it has to be a copy, and then the original is only let go once
    the copy has been weighed and found to be all there.
    """
    if not source.exists():
        raise MoveError(f"{source.name} is not there any more")
    if target.exists() and not same_file(source, target):
        raise MoveError(f"something is already called {target.name}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as problem:
        raise MoveError(f"cannot make {target.parent}: {problem.strerror}") from problem
    try:
        os.rename(source, target)
        return
    except OSError as problem:
        if problem.errno != errno.EXDEV:
            raise MoveError(f"{source.name}: {problem.strerror}") from problem

    expected = _weigh(source)
    staging = target.with_name(target.name + ".aeris-part")
    _discard(staging)
    try:
        if source.is_dir():
            shutil.copytree(source, staging, symlinks=True)
        else:
            shutil.copy2(source, staging)
        if _weigh(staging) != expected:
            raise MoveError(f"{source.name} did not copy across in one piece")
        os.replace(staging, target)
    except MoveError:
        _discard(staging)
        raise
    except OSError as problem:
        _discard(staging)
        raise MoveError(f"{source.name}: {problem.strerror}") from problem
    # Only now, with the copy checked and in place, is the original expendable.
    _discard(source)


def move_book(source: Path, target: Path, note=None) -> list[tuple[str, str, str]]:
    """Move a book and everything that belongs to it. Returns what moved.

    The book itself goes first: if it cannot, nothing else is disturbed. Its
    sidecars follow one at a time, and one that will not go is left where it is
    rather than taking the book back with it — an EPUB in the wrong folder is a
    nuisance, a half-moved book is a broken one.

    `note` is called with (before, after, kind) as each one happens, so that a
    crash half way still leaves a trail that can be walked back. `kind` is
    "move" for the book itself and "side" for the things that came with it —
    the difference matters when the index is told about it afterwards.
    """
    moved: list[tuple[str, str, str]] = []

    def record(before: Path, after: Path, kind: str) -> None:
        moved.append((str(before), str(after), kind))
        if note is not None:
            note(str(before), str(after), kind)

    followers = [(entry, target.parent / _renamed(entry, source, target))
                 for entry in sidecars(source)]
    relocate(source, target)
    record(source, target, "move")
    for entry, destination in followers:
        if destination.exists():
            # Two sidecars wanted the same new name. The second keeps its own.
            destination = target.parent / entry.name
            if destination.exists():
                continue                # leave it rather than write over anything
        try:
            relocate(entry, destination)
        except MoveError:
            continue
        record(entry, destination, "side")
    return moved


def prune(folder: Path, root: Path) -> int:
    """Take away the folders a move emptied, and stop at the library root.

    Only genuinely empty ones. A folder left holding a stray `Thumbs.db` stays
    where it is: clutter can be swept up by hand, and nothing here deletes a
    file it could not put back.
    """
    removed = 0
    root = Path(root).resolve()
    try:
        here = Path(folder).resolve()
    except OSError:
        return 0
    while here != root and root in here.parents:
        try:
            next(here.iterdir())
            return removed              # still has something in it
        except StopIteration:
            pass
        except OSError:
            return removed
        try:
            here.rmdir()
        except OSError:
            return removed
        removed += 1
        here = here.parent
    return removed


# ----------------------------------------------------------------------- tags

_MP4 = {"title": "\xa9nam", "album": "\xa9alb", "artist": "\xa9ART",
        "albumartist": "aART", "composer": "\xa9wrt", "grouping": "\xa9grp"}
_ID3 = {"title": "TIT2", "album": "TALB", "artist": "TPE1",
        "albumartist": "TPE2", "composer": "TCOM", "grouping": "TIT1"}
_VORBIS = {name: name.upper() for name in
           ("title", "album", "artist", "albumartist", "composer", "grouping")}

TAG_FIELDS = tuple(_MP4)


def _shape(tags) -> tuple[str, dict] | tuple[None, None]:
    """Which of the three tag dialects this file speaks."""
    name = type(tags).__name__
    if name == "MP4Tags":
        return "mp4", _MP4
    if name in ("ID3", "ID3Tags"):
        return "id3", _ID3
    if hasattr(tags, "get") and hasattr(tags, "__setitem__"):
        return "vorbis", _VORBIS
    return None, None


def read_tags(path: Path) -> dict:
    """The six fields tidying cares about, as they stand on disk."""
    try:
        from mutagen import File                    # noqa: PLC0415  (optional work)
        handle = File(str(path))
    except Exception:                               # noqa: BLE001  (unreadable file)
        return {}
    if handle is None or handle.tags is None:
        return {}
    dialect, keys = _shape(handle.tags)
    if dialect is None:
        return {}
    found = {}
    for field, key in keys.items():
        value = handle.tags.get(key)
        if dialect == "id3":
            value = getattr(value, "text", value)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        found[field] = str(value) if value is not None else ""
    return found


def write_tags(path: Path, values: dict) -> dict:
    """Write the given fields, leaving everything else in the file alone.

    Returns what was there before, so it can be put back. An empty value means
    "clear this field", which is what undoing a write needs.
    """
    from mutagen import File                        # noqa: PLC0415  (optional work)
    from mutagen.id3 import Frames                  # noqa: PLC0415

    handle = File(str(path))
    if handle is None:
        raise MoveError(f"{path.name} is not something with tags")
    if handle.tags is None:
        handle.add_tags()
    dialect, keys = _shape(handle.tags)
    if dialect is None:
        raise MoveError(f"{path.name} keeps its tags in a form Aeris cannot write")

    before = read_tags(path)
    for field, value in values.items():
        key = keys.get(field)
        if key is None:
            continue
        if not value:
            handle.tags.pop(key, None)
        elif dialect == "id3":
            handle.tags.setall(key, [Frames[key](encoding=3, text=[str(value)])])
        else:
            handle.tags[key] = [str(value)]
    handle.save()
    return {field: before.get(field, "") for field in values}


def tag_plan(title: str, author: str, narrator: str, series: str,
             *, whole_book: bool) -> dict:
    """What the six fields should say.

    A single file is the whole book, so its title is the book's. In a folder of
    chapters each file keeps its own title — that is the chapter's name — and
    only the album says which book they all belong to.
    """
    wanted = {"album": title, "artist": author, "albumartist": author,
              "composer": narrator, "grouping": series}
    if whole_book:
        wanted["title"] = title
    return {name: value for name, value in wanted.items() if value}


def tag_gap(files, wanted: dict) -> dict:
    """The parts of `wanted` that these files do not already say.

    Without this, a book that has been retagged still looks like it needs
    retagging, and the dialog would offer the same work for ever.
    """
    if not wanted:
        return {}
    missing: dict = {}
    for path in files:
        found = read_tags(Path(path))
        for field, value in wanted.items():
            if found.get(field, "") != value:
                missing[field] = value
        if len(missing) == len(wanted):
            break                       # nothing left to learn from the rest
    return missing


def dumps(values: dict) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def loads(text: str) -> dict:
    try:
        found = json.loads(text or "{}")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}
