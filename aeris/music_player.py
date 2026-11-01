"""The music player: a queue, a headless mpv, and the rules for moving through it."""

import random
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .audio_engine import AudioEngine

REPEAT_MODES = ("off", "all", "one")


def album_key(album: dict) -> int:
    """A stable positive id for an album, for caching its artwork."""
    return abs(hash(f"{album.get('album_artist')} {album.get('album')}")) % (2**31)


def album_cover_source(album: dict) -> dict:
    """The shape CoverCache wants, pointing at the album's own folder."""
    sample = album.get("sample_path") or ""
    folder = str(Path(sample).parent) if sample else ""
    return {
        "id": album_key(album),
        "path": folder,
        "size": int(album.get("tracks") or 0),
        "mtime": 0,
        "title": album.get("album") or "Album",
        "author": album.get("album_artist") or "",
    }


class MusicPlayer(QObject):
    """Owns the queue and the audio engine behind the music tab."""

    trackChanged = Signal(dict)              # the track now playing ({} when stopped)
    queueChanged = Signal()
    pausedChanged = Signal(bool)
    positionChanged = Signal(float, float)   # position, duration
    volumeChanged = Signal(float)
    likedChanged = Signal(int, bool)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.engine = AudioEngine(self)
        self.queue: list[dict] = []
        self.index = -1
        self.shuffle = bool(library.get("music_shuffle", False))
        self.repeat = str(library.get("music_repeat", "off"))
        self._order: list[int] = []       # positions into queue, in play order
        self._order_at = -1
        self._counted = False

        self.engine.pausedChanged.connect(self.pausedChanged)
        self.engine.positionChanged.connect(self._on_position)
        self.engine.trackEnded.connect(self.next_track)
        self.engine.volumeChanged.connect(self.volumeChanged)
        self.engine.set_volume(float(library.get("volume", 100)))
        self.engine.set_muted(bool(library.get("muted", False)))

    # ------------------------------------------------------------------ state

    @property
    def current(self) -> dict | None:
        if 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        return None

    @property
    def paused(self) -> bool:
        return self.engine.paused

    @property
    def duration(self) -> float:
        engine_duration = self.engine.duration
        if engine_duration:
            return engine_duration
        current = self.current
        return float(current.get("duration") or 0) if current else 0.0

    # ------------------------------------------------------------------ queue

    def set_queue(self, tracks: list[dict], start: int = 0, play: bool = True) -> None:
        self.queue = [dict(t) for t in tracks]
        self._rebuild_order(start if 0 <= start < len(self.queue) else 0)
        self.queueChanged.emit()
        if self.queue and play:
            self._play_at(self.index)
        elif not self.queue:
            self.stop()

    def enqueue(self, tracks: list[dict], next_up: bool = False) -> int:
        """Add tracks to the queue; returns how many were added."""
        additions = [dict(t) for t in tracks]
        if not additions:
            return 0
        was_empty = not self.queue
        if next_up and self.queue:
            at = self.index + 1
            self.queue[at:at] = additions
        else:
            self.queue.extend(additions)
        self._rebuild_order(max(self.index, 0), keep_current=not was_empty)
        self.queueChanged.emit()
        if was_empty:
            self._play_at(self.index)
        return len(additions)

    def clear_queue(self) -> None:
        self.stop()
        self.queue = []
        self._order = []
        self._order_at = -1
        self.index = -1
        self.queueChanged.emit()

    def remove_at(self, position: int) -> None:
        if not (0 <= position < len(self.queue)):
            return
        removing_current = position == self.index
        del self.queue[position]
        if not self.queue:
            self.clear_queue()
            return
        if removing_current:
            following = min(position, len(self.queue) - 1)
            self._rebuild_order(following)
            self.queueChanged.emit()
            self._play_at(self.index)
            return
        if position < self.index:
            self.index -= 1
        self._rebuild_order(self.index, keep_current=True)
        self.queueChanged.emit()

    def _rebuild_order(self, current: int, keep_current: bool = False) -> None:
        positions = list(range(len(self.queue)))
        if not positions:
            self._order, self._order_at, self.index = [], -1, -1
            return
        current = max(0, min(current, len(positions) - 1))
        if self.shuffle:
            rest = [p for p in positions if p != current]
            random.shuffle(rest)
            self._order = [current] + rest
        else:
            self._order = positions
        self._order_at = self._order.index(current)
        self.index = current

    # --------------------------------------------------------------- playback

    def _play_at(self, position: int) -> None:
        if not (0 <= position < len(self.queue)):
            return
        self.index = position
        if position in self._order:
            self._order_at = self._order.index(position)
        track = self.queue[position]
        self._counted = False
        if not Path(track["path"]).exists():
            self.trackChanged.emit(track)
            QTimer.singleShot(50, self.next_track)
            return
        self.engine.open(track["path"])
        self.trackChanged.emit(track)

    def play_track_at(self, position: int) -> None:
        self._play_at(position)

    def toggle_pause(self) -> None:
        if self.current is None and self.queue:
            self._play_at(self._order[0] if self._order else 0)
            return
        self.engine.toggle_pause()

    def stop(self) -> None:
        self.engine.stop()
        self.trackChanged.emit({})

    def next_track(self) -> None:
        if not self.queue:
            return
        if self.repeat == "one" and self.current is not None:
            self.engine.seek_absolute(0)
            self.engine.set_paused(False)
            return
        if self._order_at + 1 < len(self._order):
            self._order_at += 1
            self._play_at(self._order[self._order_at])
            return
        if self.repeat == "all":
            self._rebuild_order(self._order[0] if self._order else 0)
            self._play_at(self.index)
            return
        self.engine.set_paused(True)

    def previous_track(self) -> None:
        if not self.queue:
            return
        # Restart the track first, like every other player.
        if self.engine.position > 3:
            self.engine.seek_absolute(0)
            return
        if self._order_at > 0:
            self._order_at -= 1
            self._play_at(self._order[self._order_at])
        else:
            self.engine.seek_absolute(0)

    def seek(self, seconds: float) -> None:
        self.engine.seek_absolute(seconds)

    def set_volume(self, volume: float) -> None:
        self.engine.set_volume(volume)
        self.library.set("volume", round(volume, 1))

    def set_shuffle(self, shuffle: bool) -> None:
        self.shuffle = bool(shuffle)
        self.library.set("music_shuffle", self.shuffle)
        if self.queue:
            self._rebuild_order(max(self.index, 0), keep_current=True)
            self.queueChanged.emit()

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        self.library.set("music_repeat", self.repeat)
        return self.repeat

    # -------------------------------------------------------------- shortcuts

    def play_albums(self, albums: list[dict], shuffle_albums: bool = False) -> int:
        """Queue whole albums, each in its own track order.

        Shuffling here shuffles the *albums*: you get complete records, one after
        another, in a random order, rather than a scramble of loose tracks.
        """
        chosen = list(albums)
        if shuffle_albums:
            random.shuffle(chosen)
        tracks: list[dict] = []
        for album in chosen:
            tracks.extend(self.library.tracks(album=album["album"],
                                              album_artist=album["album_artist"],
                                              sort="album"))
        if not tracks:
            return 0
        if shuffle_albums and self.shuffle:
            self.set_shuffle(False)      # album order is the point; do not re-scramble
        self.set_queue(tracks, 0, play=True)
        return len(chosen)

    def toggle_liked(self, track: dict) -> bool:
        liked = not bool(track.get("liked"))
        self.library.set_liked(track["id"], liked)
        track["liked"] = 1 if liked else 0
        for queued in self.queue:
            if queued["id"] == track["id"]:
                queued["liked"] = track["liked"]
        self.likedChanged.emit(track["id"], liked)
        return liked

    # ----------------------------------------------------------------- events

    def _on_position(self, position: float) -> None:
        self.positionChanged.emit(position, self.duration)
        if not self._counted and position > 20:
            self._counted = True
            current = self.current
            if current:
                self.library.note_played(current["id"])

    def shutdown(self) -> None:
        self.engine.shutdown()
