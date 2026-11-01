"""Last.fm scrobbling, in-process.

A signed call to ws.audioscrobbler.com is just HTTP: parameters sorted
alphabetically, concatenated with the shared secret, MD5'd, appended as
``sig``. The session is a web-token flow, so no password is ever stored:
request a token, open the browser, the user approves, the token is exchanged
for a session key.

Scrobble rules mirror Pano's defaults: a track counts once it has been played
for half its length or four minutes, whichever comes first, and only if it is
longer than thirty seconds. "Played" means heard: time skipped by seeking, or
spent paused, does not count towards it. Submissions that fail (offline, redeployments) go
into a queue in the settings and ride along with the next scrobble.
"""

import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices

API = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
TIMEOUT = 12
# half the track, capped at four minutes — the classic scrobble threshold
_MAX_BEFORE_SCROBBLE = 240.0
_MIN_TRACK_LENGTH = 30.0
# Position reports arrive about every 64 ms. A step forward of up to this much
# between two of them is listening (with room for 2x speed and a busy UI); a
# bigger jump, or one backwards, is a seek and is not time anybody heard.
_MAX_STEP = 2.0


def _sign(params: dict, secret: str) -> str:
    plain = "".join(k + str(params[k]) for k in sorted(params))
    return hashlib.md5((plain + secret).encode("utf-8")).hexdigest()


def _post(params: dict) -> dict:
    """One signed API call; returns a small dict of what matters.

    Last.fm answers in XML by default: ``<lfm status="ok">`` or
    ``<lfm status="failed"><error code="N">message</error></lfm>``.
    """
    body = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(API, data=body, headers={
        "User-Agent": "Aerwave/3.0 (desktop music player)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            blob = response.read()
    except urllib.error.HTTPError as exc:
        blob = exc.read()          # errors arrive in the same XML envelope
    try:
        root = ET.fromstring(blob)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable Last.fm reply") from exc
    if root.get("status") not in ("ok", "accepted"):   # scrobble says "accepted"
        error = root.find("error")
        message = (error.text or "unknown error") if error is not None \
            else "unknown error"
        raise ValueError(message)
    return {child.tag: (child.text or "") for child in root.iter()
            if child is not root and len(child) == 0 and child.text}


def _get_xml(params: dict) -> ET.Element:
    """An unsigned GET (track.getInfo needs no session); returns the XML root."""
    url = API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "User-Agent": "Aerwave/3.0 (desktop music player)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            root = ET.fromstring(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, ET.ParseError) as exc:
        raise ValueError(f"Last.fm unreachable: {exc}") from exc
    if root.get("status") != "ok":
        error = root.find("error")
        raise ValueError((error.text or "unknown error") if error is not None
                         else "unknown error")
    return root


class LastfmClient:
    """The raw API: tokens, sessions, scrobbles, now-playing."""

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key.strip()
        self.secret = secret.strip()
        self.token = ""
        self.session_key = ""
        self.username = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret)

    @property
    def connected(self) -> bool:
        return bool(self.session_key)

    def request_token(self) -> str:
        self.token = _post({
            "method": "auth.getToken", "api_key": self.api_key,
        }).get("token", "")
        if not self.token:
            raise ValueError("Last.fm sent no token")
        return self.token

    def authorize_url(self) -> str:
        return (AUTH_URL + "?" + urllib.parse.urlencode(
            {"api_key": self.api_key, "token": self.token}))

    def fetch_session(self) -> str:
        """Exchange the approved token for a session key. Returns the username."""
        params = {"method": "auth.getSession", "api_key": self.api_key,
                  "token": self.token}
        data = _post({**params, "api_sig": _sign(params, self.secret)})
        self.session_key = data.get("key", "")
        self.username = data.get("name", "")
        if not self.session_key:
            raise ValueError("Last.fm sent no session key")
        return self.username

    def scrobble(self, artist: str, title: str, album: str, started_at: int,
                 duration: float = 0.0) -> None:
        params = {
            "method": "track.scrobble", "api_key": self.api_key,
            "sk": self.session_key,
            "artist[0]": artist, "track[0]": title,
            "timestamp[0]": str(int(started_at)),
        }
        if album:
            params["album[0]"] = album
        if duration:
            params["duration[0]"] = str(int(duration))
        _post({**params, "api_sig": _sign(params, self.secret)})

    def now_playing(self, artist: str, title: str, album: str,
                    duration: float = 0.0) -> None:
        params = {
            "method": "track.updateNowPlaying", "api_key": self.api_key,
            "sk": self.session_key,
            "artist": artist, "track": title,
        }
        if album:
            params["album"] = album
        if duration:
            params["duration"] = str(int(duration))
        _post({**params, "api_sig": _sign(params, self.secret)})


    def album_art_url(self, artist: str, title: str, album: str = "") -> str:
        """Cover art from Last.fm's track info — an https URL, or "".

        Works without a session; only the API key is used. The album-aware
        query is tried first, then a title-only one, since tags drift.
        """
        if not self.api_key or not artist.strip() or not title.strip():
            return ""
        queries = [{"method": "track.getInfo", "api_key": self.api_key,
                    "artist": artist, "track": title}]
        if album.strip():
            queries.insert(0, dict(queries[0], album=album))
        for query in queries:
            try:
                root = _get_xml(query)
            except ValueError:
                continue
            for image in root.iter("image"):
                if image.get("size") == "extralarge" and (image.text or "").startswith("http"):
                    return image.text.strip()
        return ""


class _Call(QRunnable):
    """One Last.fm request, off the interface thread.

    Scrobbling used to happen inline on the track-changed signal, which meant
    every song change waited on a web request — a frozen window for as long as
    the timeout if the network was slow, and far longer with a queue to flush.
    """

    def __init__(self, job):
        super().__init__()
        self.job = job
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.job()
        except RuntimeError:
            pass                       # the window closed while we were talking


class LastfmScrobbler(QObject):
    """Watches the music player and submits scrobbles when a track earns one."""

    changed = Signal()                     # connection state changed
    status = Signal(str)                   # one-line human-readable state

    def __init__(self, player, library, parent=None):
        super().__init__(parent)
        self.player = player
        self.library = library
        saved = library.get("lastfm", {})
        self.client = LastfmClient(saved.get("api_key", ""),
                                   saved.get("secret", ""))
        self.client.session_key = saved.get("session_key", "")
        self.client.username = saved.get("username", "")
        # The track being listened to, and everything a scrobble of it needs,
        # captured when it starts. The worker is handed copies: it used to
        # read the start time when its turn came, and by then the next song
        # had often begun — so a song went up with the next one's time.
        self._track: dict | None = None
        self._track_started_at = 0.0        # wall clock when playback began
        self._heard = 0.0                   # seconds of it actually played
        self._last_position: float | None = None
        self._scrobbled = False
        self._pending_now_playing = ""
        # One at a time and in order: a scrobble that has to be queued must be
        # written down before the next one reads that queue back.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        player.trackChanged.connect(self._on_track)
        player.positionChanged.connect(self._on_position)
        player.pausedChanged.connect(self._on_paused)

    # ------------------------------------------------------------------ state

    @property
    def connected(self) -> bool:
        return self.client.connected

    def credentials_summary(self) -> str:
        if self.connected:
            return f"Last.fm: {self.client.username}"
        return "Last.fm: not connected"

    def save_credentials(self, api_key: str, secret: str,
                         client_id: str) -> None:
        saved = self.library.get("lastfm", {})
        saved.update({"api_key": api_key.strip(), "secret": secret.strip(),
                      "discord_client_id": client_id.strip()})
        self.library.set("lastfm", saved)
        self.client = LastfmClient(api_key, secret)
        self.client.session_key = saved.get("session_key", "")
        self.client.username = saved.get("username", "")
        self.changed.emit()

    def start_connect(self) -> str:
        """Request a token and open the approval page. Returns the URL."""
        url = self.client.authorize_url()
        QDesktopServices.openUrl(QUrl(url))
        return url

    def finish_connect(self) -> str:
        """Exchange the (user-approved) token for a session key."""
        username = self.client.fetch_session()
        saved = self.library.get("lastfm", {})
        saved.update({"session_key": self.client.session_key,
                      "username": username})
        self.library.set("lastfm", saved)
        self.status.emit(f"Last.fm connected as {username}")
        self.changed.emit()
        self._flush_queue()
        return username

    def disconnect(self) -> None:
        saved = self.library.get("lastfm", {})
        saved.pop("session_key", "")
        saved.pop("username", "")
        self.library.set("lastfm", saved)
        self.client.session_key = ""
        self.client.username = ""
        self.changed.emit()

    # ----------------------------------------------------------------- player

    def _on_track(self, track: dict) -> None:
        if not track:
            self._track = None
            return
        self._begin(track)
        self._pending_now_playing = track["path"]
        if self.connected:
            self._later(dict(track), self._now_playing)
        self._later(None, self._flush_queue)

    def _begin(self, track: dict) -> None:
        """A play of `track` starts now: nothing heard of it yet."""
        self._track = dict(track)
        self._track_started_at = time.time()
        self._heard = 0.0
        self._last_position = None
        self._scrobbled = False

    def _later(self, payload, job) -> None:
        """Do this on the worker: everything here talks to the network."""
        self._pool.start(_Call(job if payload is None else lambda: job(payload)))

    def _on_paused(self, _paused: bool) -> None:
        pass                       # paused time is never counted as heard

    def _length(self, reported: float) -> float:
        """The track's length. The library's figure first: the player's own
        can still be the previous file's for a moment after a change."""
        known = float((self._track or {}).get("duration") or 0)
        return known if known > 0 else float(reported or 0)

    def _on_position(self, position: float, duration: float) -> None:
        """Count what is actually heard, and scrobble once it is enough.

        Scrobbling used to go on the raw position: past half way meant
        scrobble. But when a song ends the player has already moved on to
        the next one while a last few positions from the old file are still
        arriving — "82.9 s into an 83 s song" — and read against the new
        track, that made it look half played the moment it began. Counting
        the small forward steps between reports instead means stale
        positions, seeks and pauses add nothing, and a scrobble is earned by
        listening, which is what the rule has always meant.
        """
        track = self._track
        current = self.player.current
        if not track or not current or current.get("id") != track.get("id"):
            return
        last = self._last_position
        self._last_position = position
        if last is not None:
            step = position - last
            if 0 < step <= _MAX_STEP:
                self._heard += step
            elif step < 0 and position < 2.0 and self._scrobbled:
                # Back to the very start of a song already scrobbled: Repeat
                # One, or playing it again — a new listen, worth a new scrobble.
                self._begin(track)
                self._last_position = position
        if self._scrobbled or not self.connected:
            return
        length = self._length(duration)
        if length and length < _MIN_TRACK_LENGTH:
            return
        needed = min(_MAX_BEFORE_SCROBBLE, length / 2) if length else _MAX_BEFORE_SCROBBLE
        if self._heard < needed:
            return
        self._scrobbled = True
        self._later(self._scrobble_of(track), self._submit)

    def _scrobble_of(self, track: dict) -> dict:
        """Everything one scrobble says, fixed at the moment it was earned."""
        return {"artist": (track.get("artist") or track.get("album_artist") or "").strip(),
                "title": (track.get("title") or "").strip(),
                "album": track.get("album") or "",
                "started": int(self._track_started_at),
                "duration": float(track.get("duration") or 0)}

    # -------------------------------------------------------------- submitting

    def _submit(self, item: dict) -> None:
        """Send one scrobble, exactly as it was when earned; queue it on failure."""
        if not item["artist"] or not item["title"]:
            return
        try:
            self.client.scrobble(item["artist"], item["title"], item["album"],
                                 item["started"], item["duration"])
            self.status.emit(f"Scrobbled {item['artist']} — {item['title']}")
        except Exception as exc:       # network down, API errors — queue it
            queue = list(self.library.get("lastfm_queue", []))
            queue.append(item)
            self.library.set("lastfm_queue", queue[-200:])
            self.status.emit(f"Scrobble queued ({str(exc)[:60]})")

    def _now_playing(self, track: dict) -> None:
        artist = (track.get("artist") or track.get("album_artist") or "").strip()
        title = (track.get("title") or "").strip()
        if not artist or not title:
            return
        try:
            self.client.now_playing(artist, title, track.get("album") or "",
                                    float(track.get("duration") or 0))
        except Exception:
            pass                       # cosmetic; the scrobble itself matters

    def shutdown(self) -> None:
        """Drop anything queued; a request in flight has its own timeout."""
        self._pool.clear()
        self._pool.waitForDone(1500)

    def _flush_queue(self) -> None:
        queue = list(self.library.get("lastfm_queue", []))
        if not queue or not self.connected:
            return
        remaining = []
        for item in queue:
            try:
                self.client.scrobble(item["artist"], item["title"],
                                     item["album"], int(item["started"]),
                                     float(item.get("duration") or 0))
            except Exception:
                remaining.append(item)
        self.library.set("lastfm_queue", remaining)
        if len(queue) != len(remaining):
            self.status.emit(f"Flushed {len(queue) - len(remaining)} queued scrobbles")
