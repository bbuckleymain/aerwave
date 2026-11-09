"""SQLite-backed library: the video index, watch progress and app settings."""

import functools
import json
import sqlite3
import threading
import time
from pathlib import Path

from .paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    path  TEXT PRIMARY KEY,
    added REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id       INTEGER PRIMARY KEY,
    path     TEXT UNIQUE NOT NULL,
    root     TEXT NOT NULL,
    title    TEXT NOT NULL,
    duration REAL    NOT NULL DEFAULT 0,
    size     INTEGER NOT NULL DEFAULT 0,
    mtime    REAL    NOT NULL DEFAULT 0,
    width    INTEGER NOT NULL DEFAULT 0,
    height   INTEGER NOT NULL DEFAULT 0,
    added    REAL    NOT NULL DEFAULT 0,
    probed   INTEGER NOT NULL DEFAULT 0,
    missing  INTEGER NOT NULL DEFAULT 0,
    series   TEXT    NOT NULL DEFAULT '',
    season   INTEGER NOT NULL DEFAULT 0,
    episode  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS progress (
    video_id    INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    position    REAL    NOT NULL DEFAULT 0,
    watched     INTEGER NOT NULL DEFAULT 0,
    last_played REAL    NOT NULL DEFAULT 0,
    play_count  INTEGER NOT NULL DEFAULT 0,
    audio_track TEXT,
    sub_track   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_root ON videos(root);
CREATE INDEX IF NOT EXISTS idx_progress_last ON progress(last_played DESC);

-- Audiobooks. A book is either one self-contained file (.m4b) or a folder of
-- audio files played in order; either way it has one running position.
CREATE TABLE IF NOT EXISTS books (
    id       INTEGER PRIMARY KEY,
    path     TEXT UNIQUE NOT NULL,     -- the folder, or the single file
    root     TEXT NOT NULL,
    title    TEXT NOT NULL,
    author   TEXT NOT NULL DEFAULT '',
    narrator TEXT NOT NULL DEFAULT '',
    series   TEXT NOT NULL DEFAULT '',
    duration REAL    NOT NULL DEFAULT 0,
    tracks   INTEGER NOT NULL DEFAULT 0,
    size     INTEGER NOT NULL DEFAULT 0,
    mtime    REAL    NOT NULL DEFAULT 0,
    added    REAL    NOT NULL DEFAULT 0,
    probed   INTEGER NOT NULL DEFAULT 0,
    missing  INTEGER NOT NULL DEFAULT 0,
    -- What this book was called before Aerwave ever renamed or retagged it.
    -- Names are read off the shelf, so renaming a book would otherwise destroy
    -- the evidence its new name was based on and the next pass would read its
    -- own answer back. These two columns are written once and never again, so
    -- every pass sees exactly what the first pass saw.
    origin     TEXT NOT NULL DEFAULT '',
    origin_tag TEXT NOT NULL DEFAULT '',
    tidied     INTEGER NOT NULL DEFAULT 0
);

-- Every file Aerwave has moved or retagged, so a whole tidy run can be undone.
-- No foreign key: the journal has to outlive the row it talks about.
CREATE TABLE IF NOT EXISTS book_moves (
    id      INTEGER PRIMARY KEY,
    batch   INTEGER NOT NULL,         -- one run of the tidy
    book_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,         -- 'move' | 'tags'
    before  TEXT    NOT NULL,         -- the old path, or JSON of the old tags
    after   TEXT    NOT NULL,
    done    REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS book_tracks (
    id       INTEGER PRIMARY KEY,
    book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,          -- play order within the book
    path     TEXT UNIQUE NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    start    REAL NOT NULL DEFAULT 0    -- cumulative offset from the book's start
);

CREATE TABLE IF NOT EXISTS book_progress (
    book_id     INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    elapsed     REAL    NOT NULL DEFAULT 0,   -- absolute seconds into the book
    finished    INTEGER NOT NULL DEFAULT 0,
    speed       REAL    NOT NULL DEFAULT 1.0,
    last_played REAL    NOT NULL DEFAULT 0,
    play_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id      INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    elapsed REAL NOT NULL,
    label   TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL DEFAULT 0
);

-- The EPUB read alongside a book, and how its chapters line up with the
-- narration. `shift` is the offset between the two chapter lists: text chapter
-- `n + shift` belongs with audio chapter `n`. `aligned` records that someone
-- set it by hand, so a later scan never argues with them.
CREATE TABLE IF NOT EXISTS book_text (
    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
    path    TEXT    NOT NULL DEFAULT '',
    shift   INTEGER NOT NULL DEFAULT 0,
    aligned INTEGER NOT NULL DEFAULT 0,
    looked  INTEGER NOT NULL DEFAULT 0,  -- 1 once a search has been made
    rate    REAL    NOT NULL DEFAULT 0   -- words per second, learned by listening
);

-- Music. Albums and artists are derived by grouping, so they cannot drift
-- out of step with the files the way a separate table would.
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    root         TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    artist       TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT '',
    genre        TEXT NOT NULL DEFAULT '',
    year         INTEGER NOT NULL DEFAULT 0,
    track_no     INTEGER NOT NULL DEFAULT 0,
    disc_no      INTEGER NOT NULL DEFAULT 0,
    duration     REAL    NOT NULL DEFAULT 0,
    codec        TEXT NOT NULL DEFAULT '',
    bitrate      INTEGER NOT NULL DEFAULT 0,
    samplerate   INTEGER NOT NULL DEFAULT 0,
    channels     INTEGER NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    added        REAL    NOT NULL DEFAULT 0,
    probed       INTEGER NOT NULL DEFAULT 0,
    missing      INTEGER NOT NULL DEFAULT 0,
    liked        INTEGER NOT NULL DEFAULT 0,
    play_count   INTEGER NOT NULL DEFAULT 0,
    last_played  REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlists (
    id      INTEGER PRIMARY KEY,
    name    TEXT UNIQUE NOT NULL,
    created REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, position)
);

-- A place to sync music to: a drive, a virtual disk, or a mounted phone.
CREATE TABLE IF NOT EXISTS sync_targets (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    path           TEXT NOT NULL,
    fmt            TEXT NOT NULL DEFAULT 'copy',   -- copy | opus | mp3 | aac | flac
    quality        TEXT NOT NULL DEFAULT '128k',
    selection      TEXT NOT NULL DEFAULT 'liked',  -- liked | all | playlist:<id> | album:...
    delete_extra   INTEGER NOT NULL DEFAULT 0,
    layout         TEXT NOT NULL DEFAULT 'mirror',
    artwork        TEXT NOT NULL DEFAULT 'both',   -- embed | copy | both | none
    lyrics         TEXT NOT NULL DEFAULT 'copy',   -- copy | embed | both | none
    sync_on_start  INTEGER NOT NULL DEFAULT 0,
    last_sync      REAL NOT NULL DEFAULT 0,
    created        REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_artist, album, disc_no, track_no);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(album_artist);
CREATE INDEX IF NOT EXISTS idx_playlist_items ON playlist_items(playlist_id, position);
CREATE INDEX IF NOT EXISTS idx_tracks_book ON book_tracks(book_id, idx);
CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id, elapsed);
CREATE INDEX IF NOT EXISTS idx_books_root ON books(root);
CREATE INDEX IF NOT EXISTS idx_book_moves ON book_moves(batch DESC, id DESC);
"""

_BOOK_SELECT = """
SELECT b.id, b.path, b.root, b.title, b.author, b.narrator, b.series, b.duration,
       b.tracks, b.size, b.mtime, b.added, b.probed, b.missing,
       b.origin, b.origin_tag, b.tidied,
       COALESCE(p.elapsed, 0)     AS elapsed,
       COALESCE(p.finished, 0)    AS finished,
       COALESCE(p.speed, 1.0)     AS speed,
       COALESCE(p.last_played, 0) AS last_played,
       COALESCE(p.play_count, 0)  AS play_count,
       (SELECT COUNT(*) FROM bookmarks m WHERE m.book_id = b.id) AS bookmark_count
FROM books b LEFT JOIN book_progress p ON p.book_id = b.id
"""

_TRACK_SELECT = """
SELECT t.id, t.path, t.root, t.title, t.artist, t.album, t.album_artist, t.genre,
       t.year, t.track_no, t.disc_no, t.duration, t.codec, t.bitrate, t.samplerate,
       t.channels, t.size, t.mtime, t.added, t.probed, t.missing, t.liked,
       t.play_count, t.last_played
FROM tracks t
"""

TRACK_SORTS = {
    "album": ("t.album_artist COLLATE NOCASE, t.album COLLATE NOCASE, "
              "t.disc_no, t.track_no, t.title COLLATE NOCASE"),
    "title": "t.title COLLATE NOCASE",
    "artist": "t.artist COLLATE NOCASE, t.album COLLATE NOCASE, t.disc_no, t.track_no",
    "added": "t.added DESC, t.title COLLATE NOCASE",
    "played": "t.last_played DESC, t.title COLLATE NOCASE",
    "duration": "t.duration DESC",
    "year": "t.year DESC, t.album COLLATE NOCASE, t.disc_no, t.track_no",
}

BOOK_SORTS = {
    "added": "b.added DESC, b.title COLLATE NOCASE",
    "recent": "COALESCE(p.last_played,0) DESC, b.added DESC",
    "title": "b.title COLLATE NOCASE",
    "author": "b.author COLLATE NOCASE, b.title COLLATE NOCASE",
    "duration": "b.duration DESC",
    "folder": "b.path COLLATE NOCASE",
}

# Columns every query returns, in a fixed order, so rows can be turned into dicts cheaply.
_SELECT = """
SELECT v.id, v.path, v.root, v.title, v.duration, v.size, v.mtime, v.width, v.height,
       v.added, v.probed, v.missing, v.series, v.season, v.episode,
       COALESCE(p.position, 0)    AS position,
       COALESCE(p.watched, 0)     AS watched,
       COALESCE(p.last_played, 0) AS last_played,
       COALESCE(p.play_count, 0)  AS play_count,
       p.audio_track, p.sub_track
FROM videos v LEFT JOIN progress p ON p.video_id = v.id
"""

SORTS = {
    "added": "v.added DESC, v.title COLLATE NOCASE",
    "recent": "COALESCE(p.last_played,0) DESC, v.added DESC",
    "title": "v.title COLLATE NOCASE",
    "duration": "v.duration DESC",
    "folder": "v.path COLLATE NOCASE",
    "size": "v.size DESC",
    "episode": "v.series COLLATE NOCASE, v.season, v.episode, v.title COLLATE NOCASE",
}


def _when_open(default):
    """Make a method a no-op once the database is closed.

    Scan workers and repeat timers can still be in flight while the window is
    shutting down; they should quietly do nothing rather than raise.
    """
    def decorate(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if self._closed:
                return default() if callable(default) else default
            return method(self, *args, **kwargs)
        return wrapper
    return decorate


class Library:
    """Thread-safe wrapper around the library database.

    Scanner and thumbnail workers touch this from background threads, so every
    statement runs under a single lock on one shared connection.
    """

    def __init__(self, path: Path = DB_PATH):
        ensure_dirs()
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Bring an older database up to date. Safe to run on every start."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(folders)")}
        if "kind" not in columns:
            # Folders used to be video-only; label the existing ones as such.
            self._conn.execute(
                "ALTER TABLE folders ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'"
            )
        columns = {row["name"] for row in self._conn.execute(
            "PRAGMA table_info(book_text)")}
        if columns and "rate" not in columns:
            # Added when the reader learned to time itself by listening.
            self._conn.execute(
                "ALTER TABLE book_text ADD COLUMN rate REAL NOT NULL DEFAULT 0")
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(books)")}
        for name, definition in (("origin", "TEXT NOT NULL DEFAULT ''"),
                                 ("origin_tag", "TEXT NOT NULL DEFAULT ''"),
                                 ("tidied", "INTEGER NOT NULL DEFAULT 0")):
            if columns and name not in columns:
                # Added when tidying learned to rename files: see `books`.
                self._conn.execute(f"ALTER TABLE books ADD COLUMN {name} {definition}")
        columns = {row["name"] for row in self._conn.execute(
            "PRAGMA table_info(sync_targets)")}
        for name, definition in (("artwork", "TEXT NOT NULL DEFAULT 'both'"),
                                 ("lyrics", "TEXT NOT NULL DEFAULT 'copy'"),
                                 ("sync_on_start", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self._conn.execute(
                    f"ALTER TABLE sync_targets ADD COLUMN {name} {definition}")
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(videos)")}
        for name, definition in (("series", "TEXT NOT NULL DEFAULT ''"),
                                 ("season", "INTEGER NOT NULL DEFAULT 0"),
                                 ("episode", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
                # The next scan fills these in.
                self._conn.execute("UPDATE videos SET probed = 0")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    # ---------------------------------------------------------------- folders

    @_when_open(list)
    def folders(self, kind: str = "video") -> list[str]:
        with self._lock:
            return [r["path"] for r in self._conn.execute(
                "SELECT path FROM folders WHERE kind = ? ORDER BY path", (kind,)
            )]

    @_when_open(None)
    def add_folder(self, path: str, kind: str = "video") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO folders(path, added, kind) VALUES (?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind",
                (path, time.time(), kind),
            )
            self._conn.commit()

    @_when_open(None)
    def remove_folder(self, path: str, kind: str = "video", drop_contents: bool = True) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM folders WHERE path = ?", (path,))
            if drop_contents:
                table = "books" if kind == "audio" else "videos"
                self._conn.execute(f"DELETE FROM {table} WHERE root = ?", (path,))
            self._conn.commit()

    # ----------------------------------------------------------------- videos

    @_when_open(lambda: (0, False))
    def upsert_video(self, path: str, root: str, title: str, size: int, mtime: float,
                     series: str = "", season: int = 0, episode: int = 0) -> tuple[int, bool]:
        """Insert or refresh a file. Returns (video_id, needs_probe)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, size, mtime, probed FROM videos WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO videos(path, root, title, size, mtime, added, "
                    "series, season, episode) VALUES (?,?,?,?,?,?,?,?,?)",
                    (path, root, title, size, mtime, time.time(), series, season, episode),
                )
                self._conn.commit()
                return cur.lastrowid, True
            changed = int(row["size"]) != size or abs(float(row["mtime"]) - mtime) > 1
            self._conn.execute(
                "UPDATE videos SET root=?, size=?, mtime=?, missing=0, probed=?, "
                "series=?, season=?, episode=? WHERE id=?",
                (root, size, mtime, 0 if changed else row["probed"],
                 series, season, episode, row["id"]),
            )
            self._conn.commit()
            return row["id"], changed or not row["probed"]

    @_when_open(None)
    def set_probe(self, video_id: int, duration: float, width: int, height: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE videos SET duration=?, width=?, height=?, probed=1 WHERE id=?",
                (duration, width, height, video_id),
            )
            self._conn.commit()

    @_when_open(0)
    def mark_missing(self, root: str, keep_paths: set[str]) -> int:
        """Flag anything under `root` that the scan no longer found."""
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM videos WHERE root = ?", (root,)).fetchall()
            gone = [(r["id"],) for r in rows if r["path"] not in keep_paths]
            if gone:
                self._conn.executemany("UPDATE videos SET missing=1 WHERE id=?", gone)
                self._conn.commit()
            return len(gone)

    @_when_open(True)
    def anything_missing(self) -> bool:
        """Is some drive unplugged? Then caches are not safe to sweep."""
        with self._lock:
            for table in ("videos", "books", "tracks"):
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE missing=1 LIMIT 1").fetchone()
                if row is not None:
                    return True
        return False

    @_when_open(0)
    def purge_missing(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM videos WHERE missing=1")
            self._conn.commit()
            return cur.rowcount

    @_when_open(None)
    def remove_video(self, video_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
            self._conn.commit()

    @_when_open(list)
    def unprobed(self, limit: int = 5000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path FROM videos WHERE probed=0 AND missing=0 LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @_when_open(list)
    def videos(self, *, search: str = "", scope: str = "all", root: str | None = None,
               sort: str = "added", series: str | None = None) -> list[dict]:
        where, params = ["v.missing = 0"], []
        if root:
            where.append("v.root = ?")
            params.append(root)
        if series is not None:
            where.append("v.series = ?")
            params.append(series)
        if search:
            where.append("(v.title LIKE ? OR v.path LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if scope == "continue":
            where.append("COALESCE(p.position,0) > 5 AND COALESCE(p.watched,0) = 0")
        elif scope == "unwatched":
            where.append("COALESCE(p.watched,0) = 0 AND COALESCE(p.position,0) <= 5")
        elif scope == "watched":
            where.append("COALESCE(p.watched,0) = 1")
        order = SORTS.get(sort, SORTS["added"])
        if scope == "continue":
            order = SORTS["recent"]
        sql = f"{_SELECT} WHERE {' AND '.join(where)} ORDER BY {order}"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    @_when_open(None)
    def video(self, video_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"{_SELECT} WHERE v.id = ?", (video_id,)).fetchone()
        return dict(row) if row else None

    @_when_open(list)
    def series(self) -> list[dict]:
        """Every detected show, with how much of it is left to watch."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT v.series AS name,
                          COUNT(*) AS episodes,
                          SUM(CASE WHEN COALESCE(p.watched,0)=1 THEN 1 ELSE 0 END) AS watched
                   FROM videos v LEFT JOIN progress p ON p.video_id = v.id
                   WHERE v.missing = 0 AND v.series <> ''
                   GROUP BY v.series
                   ORDER BY v.series COLLATE NOCASE"""
            ).fetchall()
        return [{"name": r["name"], "episodes": r["episodes"],
                 "watched": r["watched"] or 0} for r in rows]

    @_when_open(None)
    def next_unwatched(self, series: str) -> dict | None:
        """The earliest episode of a show that has not been finished."""
        with self._lock:
            row = self._conn.execute(
                f"""{_SELECT} WHERE v.missing = 0 AND v.series = ?
                    AND COALESCE(p.watched,0) = 0
                    ORDER BY v.season, v.episode, v.title COLLATE NOCASE LIMIT 1""",
                (series,),
            ).fetchone()
        return dict(row) if row else None

    @_when_open(dict)
    def counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN COALESCE(p.watched,0)=1 THEN 1 ELSE 0 END) AS watched,
                          SUM(CASE WHEN COALESCE(p.position,0)>5 AND COALESCE(p.watched,0)=0
                                   THEN 1 ELSE 0 END) AS continuing,
                          SUM(v.duration) AS seconds
                   FROM videos v LEFT JOIN progress p ON p.video_id=v.id WHERE v.missing=0"""
            ).fetchone()
        return {k: (row[k] or 0) for k in ("total", "watched", "continuing", "seconds")}

    # --------------------------------------------------------------- progress

    @_when_open(None)
    def save_progress(self, video_id: int, position: float, watched: bool | None = None,
                      audio_track=None, sub_track=None, bump_count: bool = False) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO progress(video_id) VALUES (?)", (video_id,)
                )
            except sqlite3.IntegrityError:
                # The video left the library (rebuild, purge) while playing.
                return
            sets = ["position=?", "last_played=?"]
            params: list = [max(0.0, position), time.time()]
            if watched is not None:
                sets.append("watched=?")
                params.append(1 if watched else 0)
            if audio_track is not None:
                sets.append("audio_track=?")
                params.append(str(audio_track))
            if sub_track is not None:
                sets.append("sub_track=?")
                params.append(str(sub_track))
            if bump_count:
                sets.append("play_count=play_count+1")
            params.append(video_id)
            self._conn.execute(f"UPDATE progress SET {', '.join(sets)} WHERE video_id=?", params)
            self._conn.commit()

    @_when_open(None)
    def set_watched(self, video_id: int, watched: bool, reset_position: bool = True) -> None:
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO progress(video_id) VALUES (?)", (video_id,))
            if reset_position:
                self._conn.execute(
                    "UPDATE progress SET watched=?, position=0, last_played=? WHERE video_id=?",
                    (1 if watched else 0, time.time(), video_id),
                )
            else:
                self._conn.execute(
                    "UPDATE progress SET watched=? WHERE video_id=?", (1 if watched else 0, video_id)
                )
            self._conn.commit()

    @_when_open(None)
    def reset_progress(self, video_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM progress WHERE video_id=?", (video_id,))
            self._conn.commit()

    # ------------------------------------------------------------- audiobooks

    @_when_open(lambda: (0, False))
    def upsert_book(self, path: str, root: str, title: str, size: int, mtime: float,
                    tracks: int) -> tuple[int, bool]:
        """Insert or refresh a book. Returns (book_id, needs_probe)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, size, mtime, tracks, probed FROM books WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO books(path, root, title, size, mtime, tracks, added) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (path, root, title, size, mtime, tracks, time.time()),
                )
                self._conn.commit()
                return cur.lastrowid, True
            changed = (int(row["size"]) != size or abs(float(row["mtime"]) - mtime) > 1
                       or int(row["tracks"]) != tracks)
            self._conn.execute(
                "UPDATE books SET root=?, size=?, mtime=?, tracks=?, missing=0, probed=? "
                "WHERE id=?",
                (root, size, mtime, tracks, 0 if changed else row["probed"], row["id"]),
            )
            self._conn.commit()
            return row["id"], changed or not row["probed"]

    @_when_open(None)
    def set_book_details(self, book_id: int, *, title: str, author: str, narrator: str,
                         series: str, duration: float, tracks: list[dict]) -> None:
        """Store the probe results and replace the book's track list.

        A book that has been tidied keeps the names it was given: writing tags
        changes the file, which makes the scanner read it again, and it would
        otherwise hand back whatever the tags happen to say.
        """
        with self._lock:
            tidied = self._conn.execute(
                "SELECT tidied FROM books WHERE id=?", (book_id,)).fetchone()
            if tidied is not None and tidied["tidied"]:
                self._conn.execute(
                    "UPDATE books SET duration=?, tracks=?, probed=1 WHERE id=?",
                    (duration, len(tracks), book_id))
            else:
                self._conn.execute(
                    "UPDATE books SET title=?, author=?, narrator=?, series=?, duration=?, "
                    "tracks=?, probed=1 WHERE id=?",
                    (title, author, narrator, series, duration, len(tracks), book_id),
                )
            self._conn.execute("DELETE FROM book_tracks WHERE book_id=?", (book_id,))
            self._conn.executemany(
                "INSERT INTO book_tracks(book_id, idx, path, title, duration, start) "
                "VALUES (?,?,?,?,?,?)",
                [(book_id, t["idx"], t["path"], t["title"], t["duration"], t["start"])
                 for t in tracks],
            )
            self._conn.commit()

    @_when_open(list)
    def book_tracks(self, book_id: int) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM book_tracks WHERE book_id=? ORDER BY idx", (book_id,)
            )]

    @_when_open(list)
    def books(self, *, search: str = "", scope: str = "all", root: str | None = None,
              sort: str = "added") -> list[dict]:
        where, params = ["b.missing = 0"], []
        if root:
            where.append("b.root = ?")
            params.append(root)
        if search:
            where.append("(b.title LIKE ? OR b.author LIKE ? OR b.series LIKE ? OR b.path LIKE ?)")
            params += [f"%{search}%"] * 4
        if scope == "listening":
            where.append("COALESCE(p.elapsed,0) > 30 AND COALESCE(p.finished,0) = 0")
        elif scope == "unstarted":
            where.append("COALESCE(p.elapsed,0) <= 30 AND COALESCE(p.finished,0) = 0")
        elif scope == "finished":
            where.append("COALESCE(p.finished,0) = 1")
        order = BOOK_SORTS.get(sort, BOOK_SORTS["added"])
        if scope == "listening":
            order = BOOK_SORTS["recent"]
        sql = f"{_BOOK_SELECT} WHERE {' AND '.join(where)} ORDER BY {order}"
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]

    @_when_open(None)
    def book(self, book_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"{_BOOK_SELECT} WHERE b.id = ?", (book_id,)).fetchone()
        return dict(row) if row else None

    @_when_open(list)
    def unprobed_books(self, limit: int = 5000) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT id, path FROM books WHERE probed=0 AND missing=0 LIMIT ?", (limit,)
            )]

    @_when_open(0)
    def mark_books_missing(self, root: str, keep_paths: set[str]) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM books WHERE root=?", (root,)).fetchall()
            gone = [(r["id"],) for r in rows if r["path"] not in keep_paths]
            if gone:
                self._conn.executemany("UPDATE books SET missing=1 WHERE id=?", gone)
                self._conn.commit()
            return len(gone)

    @_when_open(0)
    def purge_missing_books(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM books WHERE missing=1")
            self._conn.commit()
            return cur.rowcount

    @_when_open(None)
    def remove_book(self, book_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            self._conn.commit()

    @_when_open(dict)
    def book_counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN COALESCE(p.finished,0)=1 THEN 1 ELSE 0 END) AS finished,
                          SUM(CASE WHEN COALESCE(p.elapsed,0)>30 AND COALESCE(p.finished,0)=0
                                   THEN 1 ELSE 0 END) AS listening,
                          SUM(b.duration) AS seconds
                   FROM books b LEFT JOIN book_progress p ON p.book_id=b.id
                   WHERE b.missing=0"""
            ).fetchone()
        return {k: (row[k] or 0) for k in ("total", "finished", "listening", "seconds")}

    @_when_open(None)
    def save_book_progress(self, book_id: int, elapsed: float, *, finished: bool | None = None,
                           speed: float | None = None, bump_count: bool = False) -> None:
        with self._lock:
            try:
                self._conn.execute("INSERT OR IGNORE INTO book_progress(book_id) VALUES (?)",
                                   (book_id,))
            except sqlite3.IntegrityError:
                return
            sets, params = ["elapsed=?", "last_played=?"], [max(0.0, elapsed), time.time()]
            if finished is not None:
                sets.append("finished=?")
                params.append(1 if finished else 0)
            if speed is not None:
                sets.append("speed=?")
                params.append(float(speed))
            if bump_count:
                sets.append("play_count=play_count+1")
            params.append(book_id)
            self._conn.execute(
                f"UPDATE book_progress SET {', '.join(sets)} WHERE book_id=?", params
            )
            self._conn.commit()

    @_when_open(None)
    def set_book_meta(self, book_id: int, **fields) -> None:
        """Correct what a book is called. Only the fields given change."""
        allowed = {name: str(value) for name, value in fields.items()
                   if name in ("title", "author", "narrator", "series") and value}
        if not allowed:
            return
        assignments = ", ".join(f"{name}=?" for name in allowed)
        with self._lock:
            self._conn.execute(f"UPDATE books SET {assignments} WHERE id=?",
                               (*allowed.values(), book_id))
            self._conn.commit()

    @_when_open(None)
    def remember_origin(self, book_id: int, path: str, tag_title: str) -> None:
        """Write down what a book was called, the once, before it is changed.

        Renaming a book destroys the evidence its new name came from, and
        retagging destroys the other half. Both are copied here first, and
        never overwritten, so tidying the same shelf twice reaches the same
        answer instead of chasing its own tail.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE books SET origin = CASE WHEN origin = '' THEN ? ELSE origin END, "
                "origin_tag = CASE WHEN tidied = 0 THEN ? ELSE origin_tag END, tidied = 1 "
                "WHERE id = ?", (path, tag_title, book_id))
            self._conn.commit()

    @_when_open(False)
    def move_book(self, book_id: int, before: str, after: str) -> bool:
        """Point a book, its tracks and its ebook at where the files now are.

        The files have already moved; this is the index catching up. Paths
        below a moved folder are rewritten by prefix, which is why `before`
        has to be the path the book had a moment ago and not something older.
        """
        with self._lock:
            try:
                self._conn.execute("UPDATE books SET path=? WHERE id=?", (after, book_id))
                old_prefix = before.rstrip("/") + "/"
                new_prefix = after.rstrip("/") + "/"
                length = len(old_prefix)
                for table in ("book_tracks", "book_text"):
                    self._conn.execute(
                        f"UPDATE {table} SET path = ? || SUBSTR(path, ?) "
                        f"WHERE book_id = ? AND SUBSTR(path, 1, ?) = ?",
                        (new_prefix, length + 1, book_id, length, old_prefix))
                self._conn.execute(
                    "UPDATE book_tracks SET path=? WHERE book_id=? AND path=?",
                    (after, book_id, before))
                self._conn.commit()
                return True
            except sqlite3.Error:
                self._conn.rollback()
                return False

    @_when_open(None)
    def repoint_text(self, book_id: int, path: str) -> None:
        """Follow an ebook that moved alongside its book."""
        with self._lock:
            self._conn.execute("UPDATE book_text SET path=? WHERE book_id=?",
                               (path, book_id))
            self._conn.commit()

    @_when_open(0)
    def start_tidy_batch(self) -> int:
        """A number for the run about to happen, so it can be undone as one."""
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(batch), 0) FROM book_moves").fetchone()
            return int(row[0]) + 1

    @_when_open(None)
    def note_change(self, batch: int, book_id: int, kind: str,
                    before: str, after: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO book_moves(batch, book_id, kind, before, after, done) "
                "VALUES (?,?,?,?,?,?)", (batch, book_id, kind, before, after, time.time()))
            self._conn.commit()

    @_when_open(None)
    def last_tidy(self) -> dict | None:
        """The most recent run that touched files, and how big it was."""
        with self._lock:
            row = self._conn.execute(
                "SELECT batch, COUNT(*) AS changes, MAX(done) AS done "
                "FROM book_moves GROUP BY batch ORDER BY batch DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    @_when_open(list)
    def tidy_changes(self, batch: int) -> list[dict]:
        """What one run did, newest first — the order it has to be undone in."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM book_moves WHERE batch=? ORDER BY id DESC", (batch,))]

    @_when_open(None)
    def forget_tidy(self, batch: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM book_moves WHERE batch=?", (batch,))
            self._conn.commit()

    @_when_open(None)
    def untidy(self, book_id: int) -> None:
        """Let a book be read off its own name again, once it is back home."""
        with self._lock:
            self._conn.execute(
                "UPDATE books SET tidied=0, origin='', origin_tag='' WHERE id=?", (book_id,))
            self._conn.commit()

    @_when_open(None)
    def reset_book_progress(self, book_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM book_progress WHERE book_id=?", (book_id,))
            self._conn.commit()

    # ------------------------------------------------------------------ music

    @_when_open(lambda: (0, False))
    def upsert_track(self, path: str, root: str, size: int, mtime: float) -> tuple[int, bool]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, size, mtime, probed FROM tracks WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    "INSERT INTO tracks(path, root, title, size, mtime, added) VALUES (?,?,?,?,?,?)",
                    (path, root, Path(path).stem, size, mtime, time.time()),
                )
                self._conn.commit()
                return cur.lastrowid, True
            changed = int(row["size"]) != size or abs(float(row["mtime"]) - mtime) > 1
            self._conn.execute(
                "UPDATE tracks SET root=?, size=?, mtime=?, missing=0, probed=? WHERE id=?",
                (root, size, mtime, 0 if changed else row["probed"], row["id"]),
            )
            self._conn.commit()
            return row["id"], changed or not row["probed"]

    @_when_open(None)
    def set_track_tags(self, track_id: int, tags: dict) -> None:
        columns = ("title", "artist", "album", "album_artist", "genre", "year",
                   "track_no", "disc_no", "duration", "codec", "bitrate",
                   "samplerate", "channels")
        assignments = ", ".join(f"{c}=?" for c in columns)
        values = [tags.get(c, "" if c in ("title", "artist", "album", "album_artist",
                                          "genre", "codec") else 0) for c in columns]
        with self._lock:
            self._conn.execute(
                f"UPDATE tracks SET {assignments}, probed=1 WHERE id=?", values + [track_id]
            )
            self._conn.commit()

    @_when_open(list)
    def unprobed_tracks(self, limit: int = 20000) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT id, path FROM tracks WHERE probed=0 AND missing=0 LIMIT ?", (limit,))]

    @_when_open(0)
    def mark_tracks_missing(self, root: str, keep_paths: set[str]) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM tracks WHERE root=?", (root,)).fetchall()
            gone = [(r["id"],) for r in rows if r["path"] not in keep_paths]
            if gone:
                self._conn.executemany("UPDATE tracks SET missing=1 WHERE id=?", gone)
                self._conn.commit()
            return len(gone)

    @_when_open(list)
    def tracks(self, *, search: str = "", album: str | None = None,
               album_artist: str | None = None, liked: bool = False,
               playlist_id: int | None = None, ids: list[int] | None = None,
               sort: str = "album", limit: int = 0) -> list[dict]:
        where, params = ["t.missing = 0"], []
        join = ""
        order = TRACK_SORTS.get(sort, TRACK_SORTS["album"])
        if playlist_id is not None:
            join = "JOIN playlist_items pi ON pi.track_id = t.id"
            where.append("pi.playlist_id = ?")
            params.append(playlist_id)
            order = "pi.position"
        if album is not None:
            where.append("t.album = ?")
            params.append(album)
        if album_artist is not None:
            where.append("t.album_artist = ?")
            params.append(album_artist)
        if liked:
            where.append("t.liked = 1")
        if ids:
            where.append(f"t.id IN ({','.join('?' * len(ids))})")
            params += list(ids)
        if search:
            where.append("(t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ? "
                         "OR t.album_artist LIKE ?)")
            params += [f"%{search}%"] * 4
        sql = f"{_TRACK_SELECT} {join} WHERE {' AND '.join(where)} ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = [dict(r) for r in self._conn.execute(sql, params)]
        if ids and sort == "given":
            order_map = {track_id: i for i, track_id in enumerate(ids)}
            rows.sort(key=lambda r: order_map.get(r["id"], 0))
        return rows

    @_when_open(None)
    def track(self, track_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(f"{_TRACK_SELECT} WHERE t.id = ?", (track_id,)).fetchone()
        return dict(row) if row else None

    @_when_open(list)
    def albums(self, *, search: str = "", album_artist: str | None = None,
               sort: str = "artist") -> list[dict]:
        where, params = ["missing = 0", "album <> ''"], []
        if album_artist is not None:
            where.append("album_artist = ?")
            params.append(album_artist)
        if search:
            where.append("(album LIKE ? OR album_artist LIKE ?)")
            params += [f"%{search}%"] * 2
        order = {
            "artist": "album_artist COLLATE NOCASE, year, album COLLATE NOCASE",
            "album": "album COLLATE NOCASE",
            "year": "year DESC, album COLLATE NOCASE",
            "added": "MAX(added) DESC",
        }.get(sort, "album_artist COLLATE NOCASE, year, album COLLATE NOCASE")
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT album, album_artist, MAX(year) AS year, COUNT(*) AS tracks,
                           SUM(duration) AS duration, MIN(path) AS sample_path,
                           SUM(liked) AS liked, MAX(added) AS added
                    FROM tracks WHERE {' AND '.join(where)}
                    GROUP BY album_artist, album ORDER BY {order}""", params).fetchall()
        return [dict(r) for r in rows]

    @_when_open(list)
    def artists(self, *, search: str = "") -> list[dict]:
        where, params = ["missing = 0", "album_artist <> ''"], []
        if search:
            where.append("album_artist LIKE ?")
            params.append(f"%{search}%")
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT album_artist AS name, COUNT(DISTINCT album) AS albums,
                           COUNT(*) AS tracks, SUM(duration) AS duration,
                           MIN(path) AS sample_path
                    FROM tracks WHERE {' AND '.join(where)}
                    GROUP BY album_artist ORDER BY album_artist COLLATE NOCASE""",
                params).fetchall()
        return [dict(r) for r in rows]

    @_when_open(dict)
    def music_counts(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) AS tracks, SUM(duration) AS seconds,
                          SUM(liked) AS liked,
                          COUNT(DISTINCT album_artist || '|' || album) AS albums,
                          COUNT(DISTINCT album_artist) AS artists
                   FROM tracks WHERE missing = 0""").fetchone()
        return {k: (row[k] or 0) for k in ("tracks", "seconds", "liked", "albums", "artists")}

    @_when_open(None)
    def set_liked(self, track_id: int, liked: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE tracks SET liked=? WHERE id=?",
                               (1 if liked else 0, track_id))
            self._conn.commit()

    @_when_open(None)
    def note_played(self, track_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tracks SET play_count=play_count+1, last_played=? WHERE id=?",
                (time.time(), track_id))
            self._conn.commit()

    # --------------------------------------------------------------- rebuild

    @_when_open(list)
    def reset_library(self) -> list[tuple[str, str]]:
        """Erase everything the scanners can rebuild from the files.

        Folders, settings and sync targets survive; playlists are wiped too,
        but first they are flattened into (name, track path) pairs which
        `restore_playlists` puts back once the rescan has re-indexed them.
        """
        with self._lock:
            saved = [(r["name"], r["path"]) for r in self._conn.execute(
                """SELECT p.name, t.path FROM playlist_items i
                   JOIN playlists p ON p.id = i.playlist_id
                   JOIN tracks t ON t.id = i.track_id
                   ORDER BY p.id, i.position""")]
            for table in ("playlist_items", "playlists", "videos", "progress",
                          "bookmarks", "book_tracks", "books", "book_progress",
                          "tracks"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.commit()
            return saved

    @_when_open(None)
    def restore_playlists(self, items: list[tuple[str, str]]) -> None:
        """Recreate the playlists flattened by `reset_library`."""
        by_playlist: dict[str, list[str]] = {}
        for name, path in items:
            by_playlist.setdefault(name, []).append(path)
        for name, paths in by_playlist.items():
            playlist_id = self.create_playlist(name)
            self.add_to_playlist(playlist_id, self._track_ids_by_path(paths))

    def _track_ids_by_path(self, paths: list[str]) -> list[int]:
        """Track ids for `paths`, keeping the given order, skipping unknown ones."""
        with self._lock:
            ids = {path: row["id"] for path, row in (
                (p, self._conn.execute(
                    "SELECT id FROM tracks WHERE path = ?", (p,)).fetchone())
                for p in paths) if row is not None}
        return [ids[p] for p in paths if p in ids]

    # -------------------------------------------------------------- playlists

    @_when_open(list)
    def playlists(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT p.id, p.name, p.created, COUNT(i.track_id) AS tracks,
                          COALESCE(SUM(t.duration), 0) AS duration
                   FROM playlists p
                   LEFT JOIN playlist_items i ON i.playlist_id = p.id
                   LEFT JOIN tracks t ON t.id = i.track_id AND t.missing = 0
                   GROUP BY p.id ORDER BY p.name COLLATE NOCASE""").fetchall()
        return [dict(r) for r in rows]

    @_when_open(0)
    def create_playlist(self, name: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO playlists(name, created) VALUES (?,?)",
                (name, time.time()))
            self._conn.commit()
            if cur.lastrowid:
                return cur.lastrowid
            row = self._conn.execute("SELECT id FROM playlists WHERE name=?", (name,)).fetchone()
            return row["id"] if row else 0

    @_when_open(None)
    def rename_playlist(self, playlist_id: int, name: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE playlists SET name=? WHERE id=?", (name, playlist_id))
            self._conn.commit()

    @_when_open(None)
    def delete_playlist(self, playlist_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            self._conn.commit()

    @_when_open(None)
    def add_to_playlist(self, playlist_id: int, track_ids: list[int]) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS last FROM playlist_items WHERE playlist_id=?",
                (playlist_id,)).fetchone()
            position = int(row["last"]) + 1
            existing = {r["track_id"] for r in self._conn.execute(
                "SELECT track_id FROM playlist_items WHERE playlist_id=?", (playlist_id,))}
            rows = []
            for track_id in track_ids:
                if track_id in existing:
                    continue
                rows.append((playlist_id, track_id, position))
                position += 1
            if rows:
                self._conn.executemany(
                    "INSERT INTO playlist_items(playlist_id, track_id, position) VALUES (?,?,?)",
                    rows)
                self._conn.commit()

    @_when_open(None)
    def remove_from_playlist(self, playlist_id: int, track_ids: list[int]) -> None:
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_items WHERE playlist_id=? AND track_id=?",
                [(playlist_id, t) for t in track_ids])
            rows = self._conn.execute(
                "SELECT track_id FROM playlist_items WHERE playlist_id=? ORDER BY position",
                (playlist_id,)).fetchall()
            self._conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            self._conn.executemany(
                "INSERT INTO playlist_items(playlist_id, track_id, position) VALUES (?,?,?)",
                [(playlist_id, r["track_id"], i) for i, r in enumerate(rows)])
            self._conn.commit()

    # ----------------------------------------------------------- sync targets

    @_when_open(list)
    def sync_targets(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM sync_targets ORDER BY name COLLATE NOCASE")]

    @_when_open(None)
    def sync_target(self, target_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sync_targets WHERE id=?",
                                     (target_id,)).fetchone()
        return dict(row) if row else None

    @_when_open(0)
    def save_sync_target(self, target: dict) -> int:
        fields = ("name", "path", "fmt", "quality", "selection", "delete_extra",
                  "layout", "artwork", "lyrics", "sync_on_start")
        values = [target.get(f, "") for f in fields]
        with self._lock:
            if target.get("id"):
                self._conn.execute(
                    f"UPDATE sync_targets SET {', '.join(f'{f}=?' for f in fields)} WHERE id=?",
                    values + [target["id"]])
                self._conn.commit()
                return int(target["id"])
            cur = self._conn.execute(
                f"INSERT INTO sync_targets({', '.join(fields)}, created) "
                f"VALUES ({','.join('?' * len(fields))}, ?)", values + [time.time()])
            self._conn.commit()
            return cur.lastrowid

    @_when_open(None)
    def delete_sync_target(self, target_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sync_targets WHERE id=?", (target_id,))
            self._conn.commit()

    @_when_open(None)
    def note_sync(self, target_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE sync_targets SET last_sync=? WHERE id=?",
                               (time.time(), target_id))
            self._conn.commit()

    # -------------------------------------------------------------- bookmarks

    @_when_open(0)
    @_when_open(dict)
    def book_text(self, book_id: int) -> dict:
        """What is known about the EPUB that goes with a book."""
        with self._lock:
            row = self._conn.execute(
                "SELECT path, shift, aligned, looked, rate FROM book_text WHERE book_id=?",
                (book_id,)).fetchone()
        return dict(row) if row else {"path": "", "shift": 0, "aligned": 0,
                                      "looked": 0, "rate": 0.0}

    @_when_open(None)
    def set_book_text(self, book_id: int, *, path: str | None = None,
                      shift: int | None = None, aligned: bool | None = None,
                      looked: bool | None = None, rate: float | None = None) -> None:
        """Remember the EPUB, or how it lines up. Only what is given changes."""
        current = self.book_text(book_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO book_text(book_id, path, shift, aligned, looked, rate) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(book_id) DO UPDATE SET "
                "path=excluded.path, shift=excluded.shift, aligned=excluded.aligned, "
                "looked=excluded.looked, rate=excluded.rate",
                (book_id,
                 current["path"] if path is None else path,
                 int(current["shift"] if shift is None else shift),
                 int(current["aligned"] if aligned is None else bool(aligned)),
                 int(current["looked"] if looked is None else bool(looked)),
                 float(current.get("rate", 0.0) if rate is None else rate)),
            )
            self._conn.commit()

    def add_bookmark(self, book_id: int, elapsed: float, label: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO bookmarks(book_id, elapsed, label, created) VALUES (?,?,?,?)",
                (book_id, elapsed, label, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    @_when_open(list)
    def bookmarks(self, book_id: int) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM bookmarks WHERE book_id=? ORDER BY elapsed", (book_id,)
            )]

    @_when_open(None)
    def remove_bookmark(self, bookmark_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
            self._conn.commit()

    @_when_open(None)
    def rename_bookmark(self, bookmark_id: int, label: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE bookmarks SET label=? WHERE id=?", (label, bookmark_id))
            self._conn.commit()

    # --------------------------------------------------------------- settings

    def get(self, key: str, default=None):
        # Not @_when_open: that would hand back None once the database has
        # closed, where the caller asked for {} or [] and will use it as one.
        if self._closed:
            return default
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    @_when_open(None)
    def set(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self._conn.commit()
