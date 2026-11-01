"""Discord Rich Presence, spoken directly to the local Discord IPC socket.

Discord listens on ``$XDG_RUNTIME_DIR/discord-ipc-N``: each frame is an
8-byte little-endian header (opcode, payload length) followed by JSON. We
hand-roll it — one dependency less — on a worker thread, so a slow or dead
socket can never touch the audio thread.

Presence shows whatever is playing — the song, artist and album; an audiobook
with its author and chapter; a video being watched — with end-of-track timing
while playing.

Discord fetches presence images from its own servers, so a cover on disk is no
use: art has to be a public URL. Songs use the one Last.fm holds, and audiobooks
one found by :mod:`aerwave.book_lookup`. Videos have no such source, so they show
none.

You need a Discord Application (discord.com/developers/applications) for the
client ID — that is what names the activity in your profile.
"""

import glob
import json
import os
import socket
import struct
import threading
import time
import uuid

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QThread, Signal

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
# Distinct from None, which is a real card meaning "showing nothing".
_NOTHING_YET = object()
_MAX_STATE = 128
LOG_LIMIT = 64 * 1024        # the presence log is a diary, not an archive


class _Worker(QObject):
    """Owns the socket; told what to display via simple slot calls."""

    state = Signal(str)                # "connected" | "off" | error text

    def __init__(self, client_id: str):
        super().__init__()
        self.client_id = client_id
        self._sock: socket.socket | None = None
        self._activity: dict | None = None
        self._dirty = True
        self._stop = False
        self._shown = _NOTHING_YET         # the last card handed over, to not repeat it
        # Woken when there is something new to show, or when it is time to go.
        # A plain sleep would mean either a laggy quit or a thread that never
        # settles; this is both instant and idle.
        self._wake = threading.Event()

    @staticmethod
    def _log(message: str) -> None:
        """Presence decisions go to a small log — the one place mysteries
        like 'why is my card blank' can be answered after the fact.

        Small is the point: something is written every time a track changes,
        so without a cap this would be the largest thing Aerwave ever wrote.
        """
        try:
            from .paths import CACHE_DIR                # noqa: PLC0415
            path = CACHE_DIR / "discord_rpc.log"
            if path.exists() and path.stat().st_size > LOG_LIMIT:
                # Keep the recent half: what went wrong was probably just now.
                tail = path.read_text(encoding="utf-8", errors="replace")[-LOG_LIMIT // 2:]
                path.write_text(tail[tail.find("\n") + 1:], encoding="utf-8")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%H:%M:%S ") + message + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ socket

    def _socket_paths(self) -> list:
        """Everywhere a Discord client might have put its IPC socket.

        Discord's own documentation says `$XDG_RUNTIME_DIR/discord-ipc-N`, and
        that is where a native install puts it. A sandboxed one cannot: it has
        its own idea of the runtime directory, and the host sees that under a
        different name — which has changed. Flatpak used to expose it at
        `app/<application-id>/`, and from 1.15 on puts it in
        `.flatpak/<application-id>/xdg-run/`, so a Flatpak upgrade quietly
        moves the socket out from under anything that only knows the old
        place. Both are searched, and so is Snap's.
        """
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base:
            # What Discord itself falls back to when there is no runtime dir.
            base = next((os.environ[name] for name in ("TMPDIR", "TMP", "TEMP")
                         if os.environ.get(name)), "/tmp")
        paths, seen = [], set()
        for pattern in (f"{base}/discord-ipc-*",
                        f"{base}/.flatpak/*/xdg-run/discord-ipc-*",
                        f"{base}/app/*/discord-ipc-*",
                        f"{base}/snap.*/discord-ipc-*"):
            for found in sorted(glob.glob(pattern)):
                if found not in seen:
                    seen.add(found)
                    paths.append(found)
        return paths

    def _connect(self) -> bool:
        candidates = self._socket_paths()
        if not candidates:
            return False
        for path in candidates:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect(path)
            except OSError:
                continue
            handshake = json.dumps({"v": 1, "client_id": self.client_id})
            try:
                self._send(sock, OP_HANDSHAKE, handshake)
                self._recv(sock)           # the ready frame
            except OSError:
                continue
            self._sock = sock
            return True
        return False

    @staticmethod
    def _send(sock: socket.socket, opcode: int, payload: str) -> None:
        blob = payload.encode("utf-8")
        sock.sendall(struct.pack("<II", opcode, len(blob)) + blob)

    @staticmethod
    def _recv(sock: socket.socket) -> dict:
        header = b""
        while len(header) < 8:
            chunk = sock.recv(8 - len(header))
            if not chunk:
                raise OSError("socket closed")
            header += chunk
        opcode, length = struct.unpack("<II", header)
        body = b""
        while len(body) < length:
            chunk = sock.recv(length - len(body))
            if not chunk:
                raise OSError("socket closed")
            body += chunk
        return json.loads(body.decode("utf-8", "replace") or "{}")

    # ------------------------------------------------------------------- loop

    def _rest(self, seconds: float) -> bool:
        """Sleep until there is something to do, or until we are told to stop."""
        self._wake.wait(seconds)
        self._wake.clear()
        return not self._stop

    def run(self) -> None:
        """The loop that owns the socket, for as long as the app is open.

        Nothing in here may raise: this runs on a thread of its own, and an
        exception would end it quietly and take presence down for the rest of
        the session with no sign of why. Anything unexpected is written down,
        the socket is dropped, and the loop tries again.
        """
        while not self._stop:
            try:
                self._work()
                return
            except Exception as problem:         # noqa: BLE001  (never die quietly)
                self._log(f"presence loop fell over, starting again: {problem!r}")
                self._drop()
                if not self._rest(5):
                    return

    def _drop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _work(self) -> None:
        last_attempt = 0.0
        while not self._stop:
            # Something new to show, or something to show and nowhere to show
            # it — the second case is Discord being started after Aerwave, which
            # would otherwise go unnoticed until the next track change.
            if self._dirty or (self._sock is None and self._activity is not None):
                if self._sock is None and time.time() - last_attempt > 15:
                    last_attempt = time.time()
                    if not self._connect():
                        self._log("no discord socket; retrying in 15s "
                                  f"(looked at {len(self._socket_paths())} place(s))")
                        self.state.emit("off")
                        self._dirty = False
                        if not self._rest(5):
                            return
                        continue
                    self._log("connected to discord")
                    self.state.emit("connected")
                if self._sock is not None:
                    try:
                        if self._activity is None:
                            payload = {"cmd": "SET_ACTIVITY",
                                       "args": {"pid": os.getpid(),
                                                "activity": None},
                                       "nonce": str(uuid.uuid4())}
                        else:
                            payload = {"cmd": "SET_ACTIVITY",
                                       "args": {"pid": os.getpid(),
                                                "activity": self._activity},
                                       "nonce": str(uuid.uuid4())}
                        self._send(self._sock, OP_FRAME, json.dumps(payload))
                        response = self._recv(self._sock)
                        if response.get("evt") not in (None, ""):
                            self._log(f"refused: {json.dumps(response)[:200]}")
                        elif self._activity is not None:
                            self._log("sent " + json.dumps(self._activity)[:220])
                        self._dirty = False
                    except OSError:
                        self._drop()
                        self._shown = _NOTHING_YET   # a new socket knows none of this
                        self.state.emit("reconnecting")
            # Nothing more until something changes — or, while a connection
            # is still owed, until it is worth another try.
            if not self._rest(5):
                return

    # ------------------------------------------------------------------ slots

    @staticmethod
    def _says_the_same(one, other) -> bool:
        """Would Discord draw these two the same card?

        Not equality: while something plays, the start and end it is told are
        recomputed from the clock each time and land a second either side of
        where they were. That is drift, not news.
        """
        if one is None or other is None:
            return one is other
        one, other = dict(one), dict(other)
        first, second = one.pop("timestamps", None), other.pop("timestamps", None)
        if one != other:
            return False
        if first is None or second is None:
            return first is second
        return all(abs(int(first.get(edge, 0)) - int(second.get(edge, 0))) <= 2
                   for edge in ("start", "end"))

    def set_activity(self, activity: dict | None) -> None:
        """Show this, unless it is what is already showing.

        The window offers an update on every position tick, and most of them
        say exactly what the last one said. Sending those anyway spends the
        allowance Discord gives for presence updates on nothing, and leaves
        none for the update that matters — which is why un-pausing used to
        take a visible moment to register.
        """
        if self._shown is not _NOTHING_YET and self._says_the_same(activity, self._shown):
            return
        self._shown = activity
        self._activity = activity
        self._dirty = True
        self._wake.set()
        if activity is not None:
            self._log("queued: " + json.dumps(activity)[:200])

    def shutdown(self) -> None:
        """Leave the loop and drop the socket. Safe to call from the GUI thread."""
        self._stop = True
        self._wake.set()
        self._drop()


class _ArtSignals(QObject):
    ready = Signal(str, str)           # request key, image url ("" when none)
    book = Signal(str, str, str)       # request key, the book's real name, cover url


class _BookArtJob(QRunnable):
    """One audiobook cover lookup, off the UI thread."""

    def __init__(self, key: str, book: dict, signals: _ArtSignals):
        super().__init__()
        self.key, self.book, self.signals = key, dict(book), signals

    def run(self) -> None:
        from .book_lookup import lookup       # noqa: PLC0415  (network, only when asked)
        try:
            name, url = lookup(self.book)
        except Exception:                      # noqa: BLE001
            name, url = "", ""
        try:
            self.signals.book.emit(self.key, name, url)
        except RuntimeError:
            pass                               # the window closed while we looked


class _LastfmArtJob(QRunnable):
    """One album-art lookup for the presence, off the UI thread."""

    def __init__(self, key: str, scrobbler, artist: str, title: str, album: str,
                 signals: _ArtSignals):
        super().__init__()
        self.key, self.scrobbler = key, scrobbler
        self.artist, self.title, self.album = artist, title, album
        self.signals = signals

    def run(self) -> None:
        try:
            url = self.scrobbler.client.album_art_url(self.artist, self.title,
                                                      self.album)
        except Exception:
            url = ""
        try:
            self.signals.ready.emit(self.key, url)
        except RuntimeError:
            pass                               # the window closed while we looked


class DiscordPresence(QObject):
    """Qt-side wrapper: builds the presence payloads, owns the worker thread."""

    state = Signal(str)

    def __init__(self, library, scrobbler=None, parent=None):
        super().__init__(parent)
        self.library = library
        self.scrobbler = scrobbler
        self._art_cache: dict[str, str] = {}   # request key -> Last.fm art url
        self._books: dict[str, dict] = {}      # book id -> {name, url}, asked for once
        self._art_signals = _ArtSignals(self)
        self._art_signals.ready.connect(self._art_ready)
        self._art_signals.book.connect(self._book_art_ready)
        self._art_pool = QThreadPool(self)
        self._art_pool.setMaxThreadCount(1)
        self._client_id = ""
        self._track: dict = {}
        self._media: dict = {}             # a book or a video, when one is playing
        self._media_kind = ""
        self._media_key = ""
        self._paused = True
        self._paused_at = 0.0              # when the pause began; Discord counts from it
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._last_push = 0.0
        self._last_position = 0.0
        self._last_wall = 0.0

    @property
    def enabled(self) -> bool:
        """Whether a worker is actually running with a client id.

        Deliberately the id the worker was started with rather than the one in
        the settings: this is asked on every position tick, and reading it back
        out of the database each time would be a query several times a second
        for an answer that changes when you change it.
        """
        return bool(self._client_id)

    @property
    def client_id(self) -> str:
        return str(self.library.get("lastfm", {}).get("discord_client_id", ""))

    def set_client_id(self, client_id: str) -> None:
        saved = self.library.get("lastfm", {})
        saved["discord_client_id"] = client_id.strip()
        self.library.set("lastfm", saved)
        self.restart()

    def restart(self) -> None:
        """(Re)start the worker with the current client id."""
        if self._thread is not None:
            if self._worker is not None:
                self._worker.shutdown()        # tell the loop to leave, then wait
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None
        self._client_id = self.client_id
        if not self._client_id:
            self.state.emit("off")
            return
        self._thread = QThread(self)
        self._worker = _Worker(self._client_id)
        self._worker.moveToThread(self._thread)
        self._worker.state.connect(self.state)
        self._thread.started.connect(self._worker.run)
        self._thread.start()
        self.update()                      # push whatever is current

    def _track_key(self, track: dict) -> str:
        artist = (track.get("album_artist") or track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()
        album = (track.get("album") or "").strip()
        return f"{artist.casefold()}|{title.casefold()}|{album.casefold()}"

    def _fetch_art(self, track: dict) -> None:
        """Ask Last.fm for the cover, unless we already know it this session."""
        if self.scrobbler is None:
            return
        key = self._track_key(track)
        if key in self._art_cache:
            return
        artist = (track.get("album_artist") or track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()
        album = (track.get("album") or "").strip()
        if not artist or not title:
            self._art_cache[key] = ""
            return
        # remember a request is out; "" from the network means no art
        self._art_cache[key] = ""
        self._art_pool.start(_LastfmArtJob(key, self.scrobbler, artist, title,
                                           album, self._art_signals))

    def _book_art_ready(self, key: str, name: str, url: str) -> None:
        found = {"name": name, "url": url}
        self._books[key] = found
        if name or url:
            self._remember_book(key, found)
            if self._media_key.startswith(f"book|{key[5:]}|"):
                self.update(force=True)

    def _art_ready(self, key: str, url: str) -> None:
        if url:
            self._art_cache[key] = url
            if self._track and self._track_key(self._track) == key:
                self.update(force=True)

    def _remember_book(self, key: str, found: dict) -> None:
        """Names and covers are worth keeping: the same books come round again."""
        stored = dict(self.library.get("discord_book_art", {}) or {})
        stored[key] = found
        if len(stored) > 300:
            stored = dict(list(stored.items())[-300:])
        self.library.set("discord_book_art", stored)

    def _known_book(self, media: dict) -> dict:
        """What is known about this book online: its real name, and a cover.

        Discord loads presence images from its own servers, so the cover on
        disk cannot be used — only a public address will do. The lookup that
        finds one also settles what the book is actually called.
        """
        key = f"book:{media.get('id')}"
        if key in self._books:
            return self._books[key]
        stored = self.library.get("discord_book_art", {}) or {}
        remembered = stored.get(key)
        if isinstance(remembered, dict):
            self._books[key] = {"name": str(remembered.get("name", "")),
                                "url": str(remembered.get("url", ""))}
            return self._books[key]
        if isinstance(remembered, str):    # an older cache held the url alone
            self._books[key] = {"name": "", "url": remembered}
            return self._books[key]
        self._books[key] = {"name": "", "url": ""}   # a request is out
        self._art_pool.start(_BookArtJob(key, media, self._art_signals))
        return self._books[key]

    def _activity_for(self) -> dict | None:
        if self._media:
            return self._media_activity()
        track = self._track
        if not track:
            return None
        artist = (track.get("artist") or track.get("album_artist") or "").strip()
        album = (track.get("album") or "").strip()
        activity = {
            "details": (track.get("title") or "Something")[:_MAX_STATE],
            "type": 2,                     # LISTENING
        }
        activity["state"] = self._second_line(artist)[:_MAX_STATE]
        if not activity["state"]:
            del activity["state"]
        art = self._art_cache.get(self._track_key(track), "")
        if art:
            # images must live under "assets" — top-level fields are ignored
            # by Discord, which then shows its question-mark placeholder
            activity["assets"] = {"large_image": art,
                                  "large_text": (album or artist)[:_MAX_STATE]}
        clock = self._clock(float(track.get("_position") or 0),
                            float(track.get("duration") or 0))
        if clock:
            activity["timestamps"] = clock
        return activity

    def _second_line(self, usual: str) -> str:
        """The line under the title: who it is by, and whether it is paused.

        Just the word. How long it has been is on the line below, where the
        time left normally goes — Discord counts that one itself, to the
        second, without being told.
        """
        usual = (usual or "").strip()
        if not self._paused or not self._paused_at:
            return usual
        return f"Paused · {usual}" if usual else "Paused"

    def _clock(self, position: float, duration: float) -> dict | None:
        """What Discord should put on its timestamp line.

        Playing, that is how much is left, which it counts down. Paused, a
        start and no end, which it counts up — and since the start is the
        moment of pausing, what it counts up is how long it has been paused.
        Discord does that itself, so a pause costs no further updates however
        long it lasts.
        """
        now = int(time.time())
        if self._paused:
            return {"start": int(self._paused_at)} if self._paused_at else None
        if not duration:
            return None
        position = max(0.0, min(position, duration))
        # start AND end is what makes Discord draw the progress bar; start
        # alone only produces a counting-up clock.
        return {"start": now - int(position), "end": now + int(duration - position)}

    def _media_activity(self) -> dict:
        """A book or a video: no Last.fm art, and videos are watched, not heard."""
        media = self._media
        title = media.get("title") or "Something"
        known = self._known_book(media) if self._media_kind == "book" else {}
        if self._media_kind == "book":
            from .book_lookup import clean_title  # noqa: PLC0415
            # The name the world knows it by beats the one in the tags, which
            # carries edition notes and is sometimes a chapter heading.
            title = known.get("name") or clean_title(title) or title
        subtitle = " · ".join(part for part in (media.get("by") or "",
                                               media.get("chapter") or "") if part)
        activity = {
            "details": title[:_MAX_STATE],
            "type": 3 if self._media_kind == "video" else 2,   # WATCHING | LISTENING
        }
        activity["state"] = self._second_line(subtitle)[:_MAX_STATE]
        if not activity["state"]:
            del activity["state"]
        if known.get("url"):
            activity["assets"] = {
                "large_image": known["url"],
                "large_text": (media.get("series") or media.get("by") or title)[:_MAX_STATE],
            }
        clock = self._clock(float(media.get("_position") or 0),
                            float(media.get("duration") or 0))
        if clock:
            activity["timestamps"] = clock
        return activity

    def update_media(self, media: dict | None, kind: str = "book") -> None:
        """Show a book or a video instead of a song, or nothing to show one again.

        Called often — every position tick — so it only pushes straight away
        when what is playing actually changed; anything else rides the throttle.
        """
        key = "" if not media else f"{kind}|{media.get('id')}|{media.get('title')}"
        changed = key != self._media_key
        position = self._media.get("_position") if not changed else None
        self._media = dict(media) if media else {}
        if position is not None and self._media:
            self._media["_position"] = position
        self._media_kind = kind if media else ""
        self._media_key = key
        self.update(force=changed)

    def update_track(self, track: dict) -> None:
        try:
            self._track = dict(track or {})
            self._track.pop("_position", None)
            self._last_position = self._last_wall = 0.0
            if self._track:
                self._fetch_art(self._track)
            self.update(force=True)
        except Exception as exc:
            self._worker and self._worker._log(f"update_track failed: {exc!r}")

    def update_position(self, position: float) -> None:
        playing = self._media or self._track
        if not playing:
            return
        now = time.time()
        # a seek moves the story further than the clock does — react at once
        jumped = (self._last_wall
                  and abs(position - self._last_position) > now - self._last_wall + 2.5)
        playing["_position"] = position
        self._last_position = position
        self._last_wall = now
        if jumped:
            self.update(force=True)
        else:
            self.update()

    def update_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return                         # said every tick; only worth a push on change
        self._paused = paused
        self._paused_at = time.time() if paused else 0.0
        self.update(force=True)

    def update(self, force: bool = False) -> None:
        # Discord throttles presence updates hard; push at most every 15 s
        # unless something structural changed (track, pause).
        now = time.time()
        if not force and now - self._last_push < 15:
            return
        self._last_push = now
        if self._worker is not None:
            self._worker.set_activity(self._activity_for())

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.shutdown()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None
