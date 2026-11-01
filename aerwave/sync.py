"""Syncing the music library to a drive, a virtual disk or a mounted phone.

All three are the same thing to Aerwave: a directory it can write to. External
drives turn up under ``/run/media``, and phones under
``/run/user/1000/gvfs/mtp:host=…`` once the file manager has mounted them.

When a target asks for a different format, files are converted **into the
target**. The conversion runs through a temporary file that is removed as soon
as it has been handed over, so no second copy of your library is ever left on
the computer.
"""

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import lyrics as lyrics_tools
from . import media
from .converter import can_embed_art, convert, extension_for
from .covers import artwork_beside
from .music_scanner import MUSIC_EXTENSIONS

# Extensions sync is allowed to create — and therefore allowed to tidy away.
# Cover images are deliberately absent: the device may hold artwork Aerwave did not
# put there, and quietly deleting someone's art is not a risk worth taking.
MANAGED_EXTENSIONS = MUSIC_EXTENSIONS | {".opus", ".mp3", ".m4a", ".flac", ".ogg", ".lrc"}
COVER_NAME = "cover.jpg"

_UNSAFE = set('<>:"\\|?*')

# What ffprobe said about each track's embedded picture, keyed by size and mtime
# so a retagged file is asked again. A sync is planned twice (in the window, then
# on the worker), and this keeps the second pass from repeating the first.
_art_codecs: dict[tuple[str, int, int], str] = {}


def safe_name(text: str, fallback: str = "Unknown") -> str:
    """A component that is legal on FAT32 and exFAT, which most devices use."""
    cleaned = "".join("_" if c in _UNSAFE or ord(c) < 32 else c for c in (text or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned[:120] or fallback


def mirrored_path(track: dict, extension: str, prefix_root: bool = False) -> str:
    """The track's own path inside its library folder, kept as it is.

    Only the extension changes, and only when the target transcodes. Returns ""
    if the file does not sit under the folder it was scanned from, which is the
    one case where the original shape cannot be worked out.
    """
    source = Path(track["path"])
    root = Path(track.get("root") or "")
    try:
        relative = source.relative_to(root)
    except ValueError:
        return ""
    parts = [safe_name(part) for part in relative.parts[:-1]]
    if prefix_root and root.name:
        parts.insert(0, safe_name(root.name))
    filename = safe_name(source.stem, "Track") + extension
    return str(Path(*parts, filename)) if parts else filename


def destination_for(track: dict, target: dict, prefix_root: bool = False) -> str:
    """Where one track belongs inside the target, relative to its root."""
    extension = extension_for(target.get("fmt", "copy"), track["path"])
    layout = target.get("layout") or "mirror"
    if layout == "mirror":
        mirrored = mirrored_path(track, extension, prefix_root)
        if mirrored:
            return mirrored
        # Nothing sensible to mirror; fall back to a tidy arrangement.
    stem = safe_name(track.get("title") or Path(track["path"]).stem, "Track")
    number = int(track.get("track_no") or 0)
    filename = f"{number:02d} {stem}{extension}" if number else f"{stem}{extension}"
    if layout == "flat":
        return filename
    artist = safe_name(track.get("album_artist") or track.get("artist"), "Unknown Artist")
    album = safe_name(track.get("album"), "Unknown Album")
    return str(Path(artist) / album / filename)


def resolve_selection(library, selection: str) -> list[dict]:
    """Which tracks a target wants, from its stored selection string."""
    selection = (selection or "liked").strip()
    if selection == "all":
        return library.tracks(sort="album")
    if selection == "liked":
        return library.tracks(liked=True, sort="album")
    if selection.startswith("playlist:"):
        try:
            return library.tracks(playlist_id=int(selection.split(":", 1)[1]))
        except (ValueError, IndexError):
            return []
    if selection.startswith("artist:"):
        return library.tracks(album_artist=selection.split(":", 1)[1], sort="album")
    if selection.startswith("album:"):
        rest = selection.split(":", 1)[1]
        artist, _, album = rest.partition("|")
        return library.tracks(album_artist=artist, album=album, sort="album")
    return []


def embedded_art(path: Path) -> str:
    """The codec of the picture inside a track ("mjpeg", "png"…), or "" if it has none."""
    try:
        info = path.stat()
    except OSError:
        return ""
    key = (str(path), info.st_size, info.st_mtime_ns)
    if key not in _art_codecs:
        result = media.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
            timeout=30)
        if result is None:                    # nothing ran: we are shutting down
            return ""
        _art_codecs[key] = (result.stdout or "").strip()
    return _art_codecs[key]


def plan_covers(root: Path, wanted: dict[str, dict]) -> list[tuple[Path, Path]]:
    """A cover.jpg for every album folder in the target that lacks a current one.

    This is decided apart from the tracks, so an album that is already on the
    device still gets its art. The picture is an image beside the tracks when
    there is one, and otherwise the one embedded in the album's first track.
    Returns (image or track, absolute cover path) pairs.
    """
    first: dict[Path, Path] = {}          # cover path -> first track headed for its folder
    for relative, track in wanted.items():
        first.setdefault((root / relative).parent / COVER_NAME, Path(track["path"]))

    covers, look_inside = [], []
    beside: dict[Path, Path | None] = {}
    for cover, source in first.items():
        if source.parent not in beside:
            beside[source.parent] = artwork_beside(source)
        image = beside[source.parent]
        try:
            present = cover.stat()
        except OSError:
            present = None
        try:
            if image is not None:
                if present is None or present.st_size != image.stat().st_size:
                    covers.append((image, cover))
            elif (present is None or present.st_size == 0
                  or present.st_mtime < source.stat().st_mtime):
                look_inside.append((source, cover))
        except OSError:
            continue
    # Looking inside a track means starting ffprobe, so only albums that still
    # need a cover are asked, a few at a time.
    with ThreadPoolExecutor(max_workers=4) as pool:
        codecs = pool.map(embedded_art, [source for source, _cover in look_inside])
        covers += [pair for pair, codec in zip(look_inside, codecs) if codec]
    return covers


@dataclass
class SyncPlan:
    """What a sync would do, worked out before anything is touched."""

    target: dict
    transfers: list = field(default_factory=list)   # (track, absolute destination)
    up_to_date: int = 0
    covers: list = field(default_factory=list)      # (image or track holding one, absolute cover.jpg)
    removals: list = field(default_factory=list)    # absolute paths to delete
    error: str = ""

    @property
    def converting(self) -> bool:
        return self.target.get("fmt", "copy") != "copy"

    def summary(self) -> str:
        if self.error:
            return self.error
        bits = []
        verb = "convert" if self.converting else "copy"
        if self.transfers:
            bits.append(f"{verb} {len(self.transfers)} track"
                        f"{'s' if len(self.transfers) != 1 else ''}")
        if self.covers:
            bits.append(f"add {len(self.covers)} album cover"
                        f"{'s' if len(self.covers) != 1 else ''}")
        if self.up_to_date:
            bits.append(f"{self.up_to_date} already there")
        if self.removals:
            bits.append(f"delete {len(self.removals)}")
        return ", ".join(bits) if bits else "nothing to do"


def _is_library(library, root: Path) -> bool:
    """Is this destination the music library itself, or part of it?

    Deleting whatever does not match is fine on a device Aerwave fills; done to
    the folder the music was scanned from it would delete the music.
    """
    try:
        here = root.resolve()
    except OSError:
        return False
    for kind in ("music", "audio", "video"):
        for folder in library.folders(kind):
            try:
                watched = Path(folder).resolve()
            except OSError:
                continue
            if here == watched or watched in here.parents or here in watched.parents:
                return True
    return False


def build_plan(library, target: dict) -> SyncPlan:
    """Work out the transfers and removals without changing anything."""
    plan = SyncPlan(target=target)
    root = Path(target.get("path", "")).expanduser()
    if not str(root) or str(root) in ("/", str(Path.home())):
        plan.error = "that destination is not a sensible place to sync to"
        return plan
    if not root.is_dir():
        plan.error = f"{root} is not available — is the device plugged in and mounted?"
        return plan
    if not os.access(root, os.W_OK):
        plan.error = f"{root} is not writable"
        return plan

    tracks = [t for t in resolve_selection(library, target.get("selection", "liked"))
              if Path(t["path"]).exists()]
    # Mirroring two library folders that both contain "Rock/…" would collide on
    # the device, so in that case each keeps its own folder name at the top.
    prefix_root = len({t.get("root") for t in tracks}) > 1
    wanted: dict[str, dict] = {}
    for track in tracks:
        wanted[destination_for(track, target, prefix_root)] = track

    for relative, track in wanted.items():
        destination = root / relative
        if destination.exists():
            try:
                fresh = destination.stat().st_mtime >= Path(track["path"]).stat().st_mtime
            except OSError:
                fresh = False
            if fresh and destination.stat().st_size > 0:
                plan.up_to_date += 1
                continue
        plan.transfers.append((track, destination))

    if target.get("artwork", "both") in ("copy", "both"):
        plan.covers = plan_covers(root, wanted)

    if target.get("delete_extra") and _is_library(library, root):
        plan.error = ("that destination is one of your music folders — "
                      "syncing into it would delete from your own library")
        return plan
    if target.get("delete_extra"):
        keep = {str(root / relative) for relative in wanted}
        # A synced lyrics file belongs to its track, so keep it too.
        keep |= {str((root / relative).with_suffix(".lrc")) for relative in wanted}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                candidate = Path(dirpath) / filename
                if candidate.suffix.lower() not in MANAGED_EXTENSIONS:
                    continue          # never touch anything Aerwave did not put there
                if str(candidate) not in keep:
                    plan.removals.append(candidate)
    return plan


class SyncWorker(QObject):
    """Runs one sync. Lives on a worker thread."""

    progress = Signal(int, int, str)            # done, total, what is happening
    finished = Signal(int, int, int, int, str)  # tracks sent, covers added, deleted, failed, error
    changed = Signal()

    def __init__(self, library, target: dict, parent=None):
        super().__init__(parent)
        self.library = library
        self.target = target
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        plan = build_plan(self.library, self.target)
        if plan.error:
            self.finished.emit(0, 0, 0, 0, plan.error)
            return
        total = len(plan.transfers) + len(plan.covers) + len(plan.removals)
        copied = covers = deleted = failed = 0
        step = 0
        fmt = self.target.get("fmt", "copy")
        quality = self.target.get("quality", "128k")
        artwork = self.target.get("artwork", "both")
        lyrics_mode = self.target.get("lyrics", "copy")

        for track, destination in plan.transfers:
            if self._cancel:
                break
            step += 1
            self.progress.emit(step, total, f"{track.get('title') or Path(track['path']).name}")
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                failed += 1
                continue

            lyrics_text = ""
            if lyrics_mode in ("embed", "both"):
                lyrics_text = lyrics_tools.raw_text(track["path"])
            art = self._art_for(track) if artwork in ("embed", "both") else None

            if fmt == "copy":
                ok = self._copy(track["path"], destination)
            else:
                ok = self._convert_onto_device(
                    track["path"], destination, fmt, quality,
                    art=art if art and can_embed_art(fmt) else None,
                    lyrics=lyrics_text or None)
            if ok:
                copied += 1
            else:
                failed += 1
                continue

            if lyrics_mode in ("copy", "both"):
                self._copy_lyrics(track, destination)

        # After the tracks, so a new album's folder exists by the time its art does.
        for source, cover in plan.covers:
            if self._cancel:
                break
            step += 1
            self.progress.emit(step, total, f"Album art for {cover.parent.name}")
            if not cover.parent.is_dir():
                continue              # none of its tracks made it across either
            if source.suffix.lower() in MUSIC_EXTENSIONS:
                ok = self._extract_cover(source, cover)
            else:
                ok = self._copy(str(source), cover)
            if ok:
                covers += 1
            else:
                failed += 1

        for path in plan.removals:
            if self._cancel:
                break
            step += 1
            self.progress.emit(step, total, f"Removing {path.name}")
            try:
                path.unlink()
                deleted += 1
            except OSError:
                failed += 1
        if not self._cancel:
            self._prune_empty_directories()
        self.library.note_sync(self.target["id"])
        self.finished.emit(copied, covers, deleted, failed, "")

    @staticmethod
    def _art_for(track: dict) -> str | None:
        """A cover image for this track's album, if one sits beside it."""
        found = artwork_beside(Path(track["path"]))
        return str(found) if found else None

    @staticmethod
    def _copy_lyrics(track: dict, destination: Path) -> None:
        """Put the .lrc next to the track it belongs to, named to match."""
        text = lyrics_tools.raw_text(track["path"])
        if not text.strip():
            return
        try:
            destination.with_suffix(".lrc").write_text(text, encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _extract_cover(track: Path, cover: Path) -> bool:
        """Take the picture out of a track and hand it over as cover.jpg.

        As with conversion, the work happens in a scratch file, so the device
        only ever receives a finished image.
        """
        handle, scratch = tempfile.mkstemp(suffix=".jpg", prefix="aerwave-cover-")
        os.close(handle)
        scratch_path = Path(scratch)
        # A JPEG comes out byte for byte; anything else is re-encoded, so that
        # cover.jpg is what its name says.
        codec = ["-c:v", "copy"] if embedded_art(track) == "mjpeg" else ["-q:v", "2"]
        try:
            result = media.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(track),
                 "-map", "0:v:0", "-frames:v", "1", *codec, str(scratch_path)],
                timeout=60, text=False)
            if result is None or result.returncode != 0 \
                    or scratch_path.stat().st_size == 0:
                return False
            shutil.copyfile(scratch_path, cover)
            return True
        except OSError:
            return False
        finally:
            scratch_path.unlink(missing_ok=True)

    @staticmethod
    def _copy(source: str, destination: Path) -> bool:
        try:
            shutil.copy2(source, destination)
            return True
        except OSError:
            return False

    @staticmethod
    def _convert_onto_device(source: str, destination: Path, fmt: str, quality: str,
                             art: str | None = None, lyrics: str | None = None) -> bool:
        """Convert into a scratch file, hand it to the device, then throw it away.

        Converting straight onto the device would be tidier, but writing a
        partial file and renaming it is exactly what MTP handles worst, so the
        work happens locally and only the finished file is transferred. Nothing
        is left behind on the computer either way.
        """
        handle, scratch = tempfile.mkstemp(suffix=destination.suffix, prefix="aerwave-sync-")
        os.close(handle)
        scratch_path = Path(scratch)
        try:
            error = convert(source, str(scratch_path), fmt, quality,
                            art=art, lyrics=lyrics)
            if error or not scratch_path.exists() or scratch_path.stat().st_size == 0:
                return False
            shutil.copy2(scratch_path, destination)
            return True
        except OSError:
            return False
        finally:
            scratch_path.unlink(missing_ok=True)

    def _prune_empty_directories(self) -> None:
        root = Path(self.target["path"])
        if not self.target.get("delete_extra"):
            return
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            here = Path(dirpath)
            if here == root or dirnames or filenames:
                continue
            try:
                here.rmdir()
            except OSError:
                pass
