"""Cover art for audiobooks: art on disk, art embedded in the file, art the
catalogue knows about, or a generated cover when a book has none of those.

The three local sources are tried in order and cost nothing. Only when all of
them come up empty is the catalogue asked, because that means sending the
book's name to Apple — so it is a setting, and a book it cannot place is
remembered as a miss rather than asked about again on every launch.
"""

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap

from . import media
from .paths import THUMB_DIR

COVER_DIR = THUMB_DIR.parent / "covers"
COVER_NAMES = ("cover", "folder", "front", "art", "albumart", "book")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
# Enough to tell whether a folder holds this book alone. Deliberately a plain
# set here rather than an import: covers must not wait on the scanner.
AUDIO_SUFFIXES = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".wav",
                  ".wma", ".aac", ".mka"}
COVER_SIZE = 500

# Deterministic palette so a given book always gets the same generated cover.
PALETTES = [
    ("#2b3a67", "#1d2440"), ("#3d2b56", "#241a33"), ("#1f4d47", "#12302c"),
    ("#5a2f3a", "#341b22"), ("#2f4858", "#1b2b35"), ("#4a3a1f", "#2b2213"),
    ("#3a2f5a", "#221c36"), ("#1f3f5a", "#132635"),
]


def artwork_beside(path: Path) -> Path | None:
    """A cover image sitting next to the book on disk.

    Which picture belongs to which book depends on whether the folder is the
    book's own. A tidy shelf puts every book of a series in one folder, so the
    lone picture in there is one book's cover and emphatically not the others';
    a folder holding a single book is the older shape, where any picture in it
    is that book's whatever it is called.
    """
    folder = path if path.is_dir() else path.parent
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    files = [entry for entry in entries if entry.is_file()]
    lowered = {entry.name.lower(): entry for entry in files}
    # A picture named after the book is certainly the book's — which is how
    # tidying leaves them when several books end up sharing one folder.
    if not path.is_dir():
        for suffix in COVER_SUFFIXES:
            found = lowered.get(f"{path.stem.lower()}{suffix}")
            if found is not None:
                return found
    alone = path.is_dir() or not any(
        entry.suffix.lower() in AUDIO_SUFFIXES and entry != path for entry in files)
    if not alone:
        return None            # someone else's picture is worse than none
    for stem in COVER_NAMES:
        for suffix in COVER_SUFFIXES:
            found = lowered.get(f"{stem}{suffix}")
            if found is not None:
                return found
    # Nothing named like a cover, but the book has this folder to itself and
    # there is exactly one picture in it. That is the cover.
    images = [entry for entry in files if entry.suffix.lower() in COVER_SUFFIXES]
    return images[0] if len(images) == 1 else None


def sweep(folder: Path, keep: set) -> int:
    """Delete the files in a cache folder that nothing points at any more.

    Anything written in the last hour is left alone: a job still running owns
    its half-finished file, and it is not in `keep` until it lands.
    """
    import time                                  # noqa: PLC0415  (housekeeping)
    cutoff = time.time() - 3600
    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file() or entry.name in keep:
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
            entry.unlink()
        except OSError:
            continue
        removed += 1
    return removed


AGENT = "Aeris/3.0 (desktop media player)"
FETCH_TIMEOUT = 12
MAX_IMAGE = 12 * 1024 * 1024          # no cover is bigger than this; nothing else is a cover


def download(url: str) -> Path | None:
    """Fetch a picture to a scratch file. Returns None if it is not one."""
    if not url.startswith("https://"):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    handle, name = tempfile.mkstemp(suffix=".img", prefix="aeris-cover-")
    target = Path(name)
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            blob = response.read(MAX_IMAGE + 1)
    except Exception:                          # noqa: BLE001  (offline, 404, redirect loop)
        os.close(handle)
        target.unlink(missing_ok=True)
        return None
    if not blob or len(blob) > MAX_IMAGE:
        os.close(handle)
        target.unlink(missing_ok=True)
        return None
    with os.fdopen(handle, "wb") as fh:
        fh.write(blob)
    return target


class _Signals(QObject):
    done = Signal(int, str)
    missed = Signal(object)                    # a book the catalogue could not place


class _CoverJob(QRunnable):
    """Find one book's cover: beside it, inside it, or in the catalogue."""

    def __init__(self, book: dict, target: Path, signals: _Signals,
                 *, online: bool = False, chosen: str = ""):
        super().__init__()
        self.book = dict(book)
        self.book_id = book["id"]
        self.source = book["path"]
        self.target = target
        self.signals = signals
        self.online = online
        self.chosen = chosen         # a picture someone picked: a file or an address
        self.setAutoDelete(True)

    def run(self) -> None:
        if self.chosen.startswith("http"):
            ok = self._fetch(self.chosen)
        elif self.chosen:
            ok = self._convert(self.chosen)
        else:
            ok = self._copy_beside() or self._extract_embedded()
            if not ok and self.online:
                ok = self._ask_catalogue()
        self.signals.done.emit(self.book_id, str(self.target) if ok else "")

    def _ask_catalogue(self) -> bool:
        from . import book_lookup                # noqa: PLC0415  (network, only when asked)
        try:
            art = book_lookup.cover_url(self.book)
        except Exception:                        # noqa: BLE001  (offline, changed API)
            art = ""
        if not art:
            try:
                self.signals.missed.emit(self.book_id)
            except RuntimeError:
                pass                             # the window closed while we looked
            return False
        return self._fetch(art)

    def _fetch(self, url: str) -> bool:
        """Download a picture and put it through the same mill as the rest."""
        scratch = download(url)
        if scratch is None:
            return False
        try:
            return self._convert(str(scratch))
        finally:
            scratch.unlink(missing_ok=True)

    def _copy_beside(self) -> bool:
        found = artwork_beside(Path(self.source))
        if found is None:
            return False
        return self._convert(str(found))

    def _extract_embedded(self) -> bool:
        """Pull the attached picture out of a container, or the first file of a folder."""
        source = Path(self.source)
        if source.is_dir():
            audio = sorted(p for p in source.iterdir()
                           if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES)
            if not audio:
                return False
            source = audio[0]
        return self._convert(str(source), attached=True)

    def _convert(self, source: str, attached: bool = False) -> bool:
        temp = self.target.with_suffix(".part.jpg")
        command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", source]
        if attached:
            command += ["-map", "0:v:0", "-frames:v", "1"]
        else:
            command += ["-frames:v", "1"]
        command += ["-vf", f"scale={COVER_SIZE}:{COVER_SIZE}:force_original_aspect_ratio=increase,"
                           f"crop={COVER_SIZE}:{COVER_SIZE}",
                    "-q:v", "3", "-y", str(temp)]
        if media.run(command, timeout=45) is None:
            temp.unlink(missing_ok=True)
            return False
        if temp.exists() and temp.stat().st_size > 0:
            temp.replace(self.target)
            return True
        temp.unlink(missing_ok=True)
        return False


class CoverCache(QObject):
    """Cover pixmaps for books, extracted in the background and cached on disk."""

    ready = Signal(int)

    def __init__(self, parent=None, library=None):
        super().__init__(parent)
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        self.library = library
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._signals.missed.connect(self._on_missed)
        self._pixmaps: dict[int, QPixmap] = {}
        # Keyed by the text drawn on it, so a book that gains an author on the
        # second scan pass gets a fresh cover instead of the stale one.
        self._generated: dict[tuple[str, str], QPixmap] = {}
        self._queued: set[int] = set()
        self._failed: set[int] = set()
        # Books the catalogue has already failed to place. Kept in the settings
        # so a shelf of obscure recordings does not send the same fruitless
        # requests on every launch; "Find cover art…" ignores it.
        self._missed: set = set(
            (library.get("book_art_misses", []) if library else []) or [])

    @property
    def online(self) -> bool:
        """Whether a book with no art of its own may be looked up."""
        return bool(self.library.get("fetch_book_art", True)) if self.library else False

    def path_for(self, book: dict) -> Path:
        key = hashlib.sha1(
            f"{book['path']}|{book.get('size', 0)}|{int(book.get('mtime', 0))}".encode()
        ).hexdigest()[:20]
        return COVER_DIR / f"{key}.jpg"

    def get(self, book: dict, *, online: bool = False) -> QPixmap:
        """The book's artwork, or a generated stand-in while it is found.

        `online` says this really is a book and not a music album, which shares
        this cache and has Last.fm for its art.
        """
        book_id = book["id"]
        pixmap = self._pixmaps.get(book_id)
        if pixmap is not None:
            return pixmap
        target = self.path_for(book)
        if target.exists():
            loaded = QPixmap(str(target))
            if not loaded.isNull():
                self._pixmaps[book_id] = loaded
                return loaded
            target.unlink(missing_ok=True)
        self.request(book, online=online)
        return self.generated(book)

    def has_artwork(self, book: dict) -> bool:
        return book["id"] in self._pixmaps

    def request(self, book: dict, *, online: bool = False, chosen: str = "") -> None:
        book_id = book["id"]
        if not chosen and (book_id in self._queued or book_id in self._failed):
            return
        self._queued.add(book_id)
        self._pool.start(_CoverJob(
            book, self.path_for(book), self._signals, chosen=chosen,
            online=online and self.online and book_id not in self._missed))

    def adopt(self, book: dict, source: str) -> None:
        """Use this picture as the book's cover, from a file or an address.

        It replaces whatever was there and goes through the same scaling as
        every other cover, so the shelf stays even. `ready` says when it has.
        """
        self.forget(book)
        self.request(book, chosen=str(source))

    def forget(self, book: dict) -> None:
        self._pixmaps.pop(book["id"], None)
        self._generated.pop((book["title"], book.get("author") or ""), None)
        self._failed.discard(book["id"])
        self._missed.discard(book["id"])
        self._save_misses()
        self.path_for(book).unlink(missing_ok=True)

    def retry_missed(self) -> None:
        """Let every book the catalogue could not place be asked about again."""
        self._missed.clear()
        self._failed.clear()
        self._save_misses()

    def _on_missed(self, book_id) -> None:
        """The catalogue has nothing; do not ask again next time either."""
        self._missed.add(book_id)
        self._save_misses()

    def _save_misses(self) -> None:
        if self.library is not None:
            self.library.set("book_art_misses", sorted(self._missed)[-2000:])

    def generated(self, book: dict) -> QPixmap:
        """A typeset cover: title, author, and a colour picked from the title."""
        key = (book["title"], book.get("author") or "")
        cached = self._generated.get(key)
        if cached is not None:
            return cached
        size = 400
        # Not hash(): Python salts string hashing per process, so the
        # "deterministic" palette would have been a different colour on every
        # start. A digest of the title is the same one every time.
        digest = hashlib.sha1(book["title"].encode("utf-8", "ignore")).digest()
        top, bottom = PALETTES[digest[0] % len(PALETTES)]
        pixmap = QPixmap(size, size)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        gradient = QLinearGradient(0, 0, size, size)
        gradient.setColorAt(0, QColor(top))
        gradient.setColorAt(1, QColor(bottom))
        painter.fillRect(0, 0, size, size, gradient)

        painter.setPen(QColor(255, 255, 255, 30))
        painter.drawLine(34, 0, 34, size)   # a spine, so it reads as a book

        title_font = QFont(painter.font())
        title_font.setPixelSize(34)
        title_font.setWeight(QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(QRectF(56, 60, size - 100, size - 190),
                         int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                         | int(Qt.TextFlag.TextWordWrap),
                         book["title"])
        author = book.get("author") or ""
        if author:
            author_font = QFont(painter.font())
            author_font.setPixelSize(22)
            author_font.setWeight(QFont.Weight.Normal)
            painter.setFont(author_font)
            painter.setPen(QColor(255, 255, 255, 165))
            painter.drawText(QRectF(56, size - 92, size - 100, 60),
                             int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                             | int(Qt.TextFlag.TextWordWrap),
                             author)
        painter.end()
        self._generated[key] = pixmap
        return pixmap

    def _on_done(self, book_id: int, path: str) -> None:
        self._queued.discard(book_id)
        if not path:
            self._failed.add(book_id)
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._failed.add(book_id)
            return
        self._pixmaps[book_id] = pixmap
        self.ready.emit(book_id)

    def sweep(self, books: list) -> int:
        """Throw away cached covers no book asks for any more.

        The cache is keyed by a book's path, size and date, so renaming a book
        or replacing its file leaves the old picture behind for ever. Nothing
        here is anything but a copy of something else.
        """
        return sweep(COVER_DIR, {self.path_for(book).name for book in books})

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)
