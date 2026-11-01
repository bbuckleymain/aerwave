"""Filling in lyrics: one pass over the library, on a worker thread.

For every track that has no synced lyrics anywhere — no synced ``.lrc`` beside
it, nothing synced in its tags — LRCLIB is asked for the words. A synced answer
replaces whatever unsynced sidecar is sitting there and goes into the file's
lyrics tag as well. When LRCLIB only has plain text, that is used instead: it
becomes the sidecar if none exists, and is embedded only when the file carries
no lyrics tag of its own, so hand-written tags are never overwritten.

Tracks LRCLIB cannot identify are remembered in the settings, so one lookup
failure doesn't turn into a fresh network request on every launch — and the
same goes for files whose tag is known to carry synced lyrics already, and
for songs LRCLIB has already answered, in any form. A hand-picked "Fetch
lyrics" run ignores every cache and asks again.
"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import lrclib, lyrics, musixmatch, netease

# Politeness pause between LRCLIB requests; the database is a free service.
_REQUEST_PAUSE = 0.25
_MISS_SAVE_EVERY = 25


def _embed(track_path: str, text: str, name: str) -> None:
    """Embed, reporting a failure on stderr — the sidecar is already written,
    so this never blocks the run, but it must not vanish without a trace."""
    if lyrics.rank(lyrics.embedded_text(track_path)) > lyrics.rank(text):
        return                         # what is in there is better; leave it
    error = embed_lyrics(track_path, text)
    if error:
        print(f"aerwave: could not embed lyrics into {name}: {error}", file=sys.stderr)


def write_sidecar(track_path: str, text: str, *, only_if_better: bool = True) -> bool:
    """Put `text` into the .lrc beside the track.

    Never a downgrade. Lyrics timed word by word are hard to come by — they
    may well have been fetched by something other than Aerwave, or corrected by
    hand — and replacing a set of those with the line-timed version LRCLIB
    happens to hold would be a loss with no way back. Equal standing is
    replaced, because the newer one is at least as good and may be a better
    transcription.
    """
    sidecar = Path(track_path).with_suffix(".lrc")
    if only_if_better and sidecar.is_file():
        try:
            having = lyrics.rank(sidecar.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            having = -1
        if having > lyrics.rank(text):
            return False
    if not text.endswith("\n"):
        text += "\n"
    try:
        sidecar.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def embed_lyrics(track_path: str, text: str) -> str:
    """Write `text` into the file's own lyrics tag.

    Returns "" on success or when the container has no standard lyrics tag
    (the sidecar is then the only copy), or a message describing the failure.
    """
    try:
        from mutagen.flac import FLAC
        from mutagen.id3 import ID3, USLT
        from mutagen.mp4 import MP4
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis
    except ImportError:
        return "mutagen is not installed"

    suffix = Path(track_path).suffix.lower()
    try:
        if suffix == ".mp3":
            tags = ID3(track_path)
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
            tags.save(track_path)
        elif suffix in (".m4a", ".mp4", ".alac"):
            audio = MP4(track_path)
            audio["\xa9lyr"] = [text]
            audio.save()
        elif suffix == ".flac":
            audio = FLAC(track_path)
            audio["LYRICS"] = text
            audio.save()
        elif suffix == ".opus":
            audio = OggOpus(track_path)
            audio["LYRICS"] = text
            audio.save()
        elif suffix in (".ogg", ".oga"):
            audio = OggVorbis(track_path)
            audio["LYRICS"] = text
            audio.save()
    except Exception as exc:                      # mutagen raises many shapes
        return str(exc)[:160]
    return ""


class LyricsFetcher(QObject):
    """One lyrics pass, on a worker thread.

    Signals mirror the scanner workers so ScanController can run this the same
    way: `progress` reports where we are, `changed` asks for a library reload
    once in a while, and `finished` counts tracks that gained synced lyrics and
    tracks that had to settle for plain ones.
    """

    progress = Signal(int, int, str)
    finished = Signal(int, int)
    changed = Signal()

    def __init__(self, library, roots=None, parent=None):
        super().__init__(parent)
        self.library = library
        self.roots = roots             # unused: lyrics follow the whole library
        self.payload = None            # an explicit track list from the UI
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _sidecar_state(self, track_path: str) -> tuple[bool, str]:
        """(synced, text) of the .lrc beside the track, if it has one."""
        sidecar = lyrics.sidecar_for(track_path)
        if sidecar is None:
            return False, ""
        try:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False, ""
        return lyrics.parse_lrc(text).synced, text

    @property
    def musixmatch_key(self) -> str:
        """The person's own Musixmatch API key, if they have given one."""
        return str(self.library.get("musixmatch_key", "") or "").strip()

    @property
    def want_words(self) -> bool:
        """Whether to go looking for lyrics timed word by word."""
        return bool(self.library.get("fetch_word_lyrics", True))

    def run(self) -> None:
        tracks = self.payload if self.payload is not None else self.library.tracks()
        total = len(tracks)
        synced_count = plain_count = 0
        misses = set(self.library.get("lrclib_misses", []))
        done = set(self.library.get("lrclib_done", []))
        # path -> mtime of the audio file at the moment we knew its embedded
        # tag carries synced lyrics, so the next pass probes nothing.
        embedded_known: dict[str, float] = dict(self.library.get("lrclib_embedded", {}))
        # A hand-picked run ("Fetch lyrics" in the track menu) retries those
        # tracks whatever the caches say; only automatic passes trust them.
        auto = self.payload is None
        # "Re-download every lyric" sets this. The caches exist to keep a
        # second launch from asking the same questions again, so a deliberate
        # redownload has to forget all three of them and look past the lyrics
        # already sitting beside, and inside, every file. Nothing is
        # downgraded even so: write_sidecar still refuses to put line-timed
        # words over word-timed ones.
        redo = bool(self.library.get("lyrics_redo", False))
        if redo:
            self.library.set("lyrics_redo", False)
            misses, done, embedded_known = set(), set(), {}
            dirty_misses = dirty_cache = dirty_done = True
        else:
            dirty_misses = dirty_cache = dirty_done = False
        # Asked first when there is a key, and dropped for the rest of the
        # pass the moment it says the key or its plan is the problem: the
        # answer would be the same for every song after.
        mxm_key = self.musixmatch_key

        for index, track in enumerate(tracks, 1):
            if self._cancel:
                break
            path = track["path"]
            name = track.get("title") or Path(path).stem
            if index % 5 == 0 or index == total:
                self.progress.emit(index, total, name)
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                continue

            artist = (track.get("album_artist") or track.get("artist") or "").strip()
            title = name.strip()
            key = f"{artist.casefold()}|{title.casefold()}"

            sidecar_synced, sidecar_text = self._sidecar_state(path)
            if sidecar_synced and not redo:
                # The words are already beside the file — no need to ask
                # LRCLIB. But an mp3 and a flac of the same song share one
                # .lrc, so the file itself may still carry nothing: check its
                # tag once, then trust that answer for this version of the file.
                if embedded_known.get(path) != mtime:
                    embedded = lyrics.embedded_text(path)
                    if lyrics.parse_lrc(embedded).synced:
                        embedded_known[path] = mtime
                    else:
                        _embed(path, sidecar_text, name)
                        try:
                            embedded_known[path] = Path(path).stat().st_mtime
                        except OSError:
                            pass
                        self.changed.emit()
                    dirty_cache = True
                continue

            if auto and (key in misses or key in done):
                continue
            embedded = lyrics.embedded_text(path)
            if lyrics.parse_lrc(embedded).synced and not redo:
                embedded_known[path] = mtime
                dirty_cache = True
                continue

            # NetEase first when words are wanted: it is the only free source
            # that times them, and a word-timed answer makes asking LRCLIB —
            # which never has them — pointless.
            # Best first. Musixmatch, when there is a key: its word timings
            # are the most complete anywhere. Then NetEase, the free source of
            # word timings. Then Musixmatch's line timings, and LRCLIB last.
            found = None
            duration = track.get("duration") or 0.0
            mxm_lines = ""
            if mxm_key:
                try:
                    timed = musixmatch.fetch(mxm_key, artist, title, duration)
                except musixmatch.PlanError as refusal:
                    mxm_key = ""
                    timed = ""
                    self.library.set("musixmatch_status", str(refusal))
                    print(f"aerwave: Musixmatch: {refusal}; not asking it again "
                          "this pass", file=sys.stderr)
                rank = lyrics.rank(timed)
                if rank == 2:
                    found = (timed, "")
                elif rank == 1:
                    mxm_lines = timed
                time.sleep(_REQUEST_PAUSE)
            if found is None and self.want_words:
                worded = netease.fetch(artist, title, duration)
                if lyrics.rank(worded) == 2:
                    found = (worded, "")
                time.sleep(_REQUEST_PAUSE)
            if found is None and mxm_lines:
                found = (mxm_lines, "")
            if found is None:
                found = lrclib.fetch(artist, title, track.get("album") or "",
                                     track.get("duration") or 0.0)
            if found is None:
                # LRCLIB is unreachable; stop rather than grind through the
                # library marking everything a miss.
                break
            synced, plain = found
            time.sleep(_REQUEST_PAUSE)

            if synced or plain:
                # Research completed for this song, whatever form it took —
                # automatic passes will never ask LRCLIB for it again.
                done.add(key)
                misses.discard(key)
                dirty_done = True

            if synced:
                if write_sidecar(path, synced):
                    synced_count += 1
                _embed(path, synced, name)
                try:
                    embedded_known[path] = Path(path).stat().st_mtime
                except OSError:
                    pass
                dirty_cache = True
                self.changed.emit()
            elif plain:
                # No synced version anywhere: keep, don't clobber, the plain
                # sidecar the track already has, and only fill an empty tag.
                if lyrics.sidecar_for(path) is None and write_sidecar(path, plain):
                    plain_count += 1
                if not embedded.strip():
                    _embed(path, plain, name)
                self.changed.emit()
            else:
                misses.add(key)
                dirty_misses = True
                if len(misses) % _MISS_SAVE_EVERY == 0:
                    self.library.set("lrclib_misses", sorted(misses))

        if dirty_misses:
            self.library.set("lrclib_misses", sorted(misses))
        if dirty_done:
            self.library.set("lrclib_done", sorted(done))
        if dirty_cache:
            if self.payload is None:
                # A whole-library pass may prune; a hand-picked one must not.
                keep = {t["path"] for t in tracks}
                embedded_known = {p: m for p, m in embedded_known.items() if p in keep}
            self.library.set("lrclib_embedded", embedded_known)
        self.changed.emit()
        self.finished.emit(synced_count, plain_count)
