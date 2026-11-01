"""Artist photos for the full-screen stage.

Deezer's public search needs no key and answers with the artist's promo
photograph (``picture_xl``, 1000x1000) — portraits and live shots, not album
covers. Each artist is downloaded once into the cache directory; a lookup that
comes up empty leaves a ``.miss`` marker so it isn't retried every track change.
"""

import hashlib
import json
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .paths import CACHE_DIR

ART_DIR = CACHE_DIR / "artists"
TIMEOUT = 12
USER_AGENT = "Aeris/3.0 (desktop music player)"
_MIN_BYTES = 2048          # a real photo is never a tiny placeholder
_MAGIC = (b"\xff\xd8\xff", b"\x89P")   # JPEG, PNG


class _Signals(QObject):
    ready = Signal(str, str)       # artist name, image path ("" when none)


class _ArtJob(QRunnable):
    """One artist: cache check, API search, image download."""

    def __init__(self, artist: str, signals: _Signals):
        super().__init__()
        self.artist = artist
        self.signals = signals

    def run(self) -> None:
        try:
            ART_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.signals.ready.emit(self.artist, "")
            return
        digest = hashlib.sha1(self.artist.casefold().encode("utf-8")).hexdigest()[:16]
        cached = ART_DIR / f"{digest}.jpg"
        miss = ART_DIR / f"{digest}.miss"
        if cached.is_file():
            self.signals.ready.emit(self.artist, str(cached))
            return
        if miss.is_file():
            self.signals.ready.emit(self.artist, "")
            return

        image = self._download()
        if image is not None:
            try:
                cached.write_bytes(image)
                self.signals.ready.emit(self.artist, str(cached))
                return
            except OSError:
                pass
        try:
            miss.write_text("")
        except OSError:
            pass
        self.signals.ready.emit(self.artist, "")

    def _download(self) -> bytes | None:
        """The photo bytes, or None on any trouble."""
        try:
            api = ("https://api.deezer.com/search/artist?limit=10&q="
                   + urllib.parse.quote(self.artist))
            request = urllib.request.Request(api, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            url = self._best_picture(data.get("data") or [])
            if not url:
                return None
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                blob = response.read()
        except (OSError, ValueError, KeyError, IndexError):
            return None
        if len(blob) < _MIN_BYTES or not blob.startswith(_MAGIC):
            return None
        return blob

    def _best_picture(self, hits: list) -> str | None:
        """The photo of the artist actually meant.

        Deezer's ranking puts tiny same-name acts ahead of the famous one, so
        exact name matches win first and fan count breaks the rest apart.
        """
        usable = [h for h in hits if isinstance(h, dict)
                  and (h.get("picture_xl") or h.get("picture_big"))]
        if not usable:
            return None
        wanted = self.artist.casefold()
        exact = [h for h in usable if h.get("name", "").casefold() == wanted]
        pool = exact or usable
        best = max(pool, key=lambda h: h.get("nb_fan") or 0)
        return best.get("picture_xl") or best.get("picture_big")


class ArtistArtFetcher(QObject):
    """Queues artist-photo lookups on worker threads, one answer each."""

    ready = Signal(str, str)       # artist name, image path ("" when none)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals(self)
        self._signals.ready.connect(self._finished)
        self._signals.ready.connect(self.ready)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._pending: set[str] = set()

    def fetch(self, artist: str) -> None:
        artist = (artist or "").strip()
        if not artist or artist in self._pending:
            return
        self._pending.add(artist)
        self._pool.start(_ArtJob(artist, self._signals))

    def shutdown(self) -> None:
        """Drop anything queued; a lookup in flight has its own timeout."""
        self._pool.clear()
        self._pool.waitForDone(1500)

    def _finished(self, artist: str, _path: str) -> None:
        self._pending.discard(artist)
