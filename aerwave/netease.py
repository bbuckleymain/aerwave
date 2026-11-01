"""NetEase Cloud Music: the one free source of word-by-word lyrics.

LRCLIB, which Aerwave asks first, carries lyrics timed by the line. Nobody who
has sung along to a karaoke view wants that, and the only other obvious source
— Musixmatch — keeps its word timings behind a commercial plan. NetEase has
them, needs no key, and can be searched by artist and title.

Its format is its own. A line reads::

    [29210,4980](29210,210,0)Have (29420,150,0)you (29570,330,0)got

— a line start and length in milliseconds, then every word with its own start
and length. That converts cleanly into the enhanced LRC the rest of Aerwave
already understands, so nothing downstream needs to know where it came from.

A word-timed lyric for the wrong song is worse than none, so a result has to
look like the track before it is accepted: the same words in the title, the
artist in common, and a length that agrees.
"""

import json
import re
import urllib.parse
import urllib.request

SEARCH = "https://music.163.com/api/search/get"
LYRIC = "https://music.163.com/api/song/lyric"
AGENT = "Mozilla/5.0 (X11; Linux x86_64) Aerwave/3.0"
REFERER = "https://music.163.com"
TIMEOUT = 10
LIMIT = 6
CLOSE_ENOUGH = 8.0             # seconds of difference still counted as a match

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# (start, length, 0)text — one word of a line.
_PIECE = re.compile(r"\((\d+),(\d+),\d+\)([^(]*)")
# [start,length] — the line itself.
_LINE = re.compile(r"^\[(\d+),(\d+)\]")


def _ask(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT,
                                                   "Referer": REFERER})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except Exception:                  # noqa: BLE001  (offline, rate limited, changed)
        return None


def _words(text: str) -> set:
    return {word.lower() for word in _WORD.findall(text or "") if len(word) > 1}


def _stamp(milliseconds: int) -> str:
    seconds, hundredths = divmod(max(0, int(milliseconds)) // 10, 100)
    return f"{seconds // 60:02d}:{seconds % 60:02d}.{hundredths:02d}"


# NetEase opens most lyrics with a block of credits — "作词 : Alex Turner",
# "编曲 : …" — timed like any other line. They are not the song.
_CREDIT = re.compile(r"^[\u4e00-\u9fff][\u4e00-\u9fff\s]{0,8}\s*[:：]")


def to_lrc(yrc: str) -> str:
    """NetEase's word timings, as the enhanced LRC Aerwave reads everywhere else."""
    out = []
    for raw in (yrc or "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("{"):
            continue                   # the credits block, which is JSON
        head = _LINE.match(raw)
        if not head:
            continue
        pieces = _PIECE.findall(raw[head.end():])
        if not pieces:
            continue
        spoken = "".join(text for _s, _l, text in pieces).strip()
        if not out and _CREDIT.match(spoken):
            continue                   # still in the credits at the top
        line = [f"[{_stamp(int(head.group(1)))}]"]
        for start, _length, text in pieces:
            text = text.strip()
            if not text:
                continue
            # Punctuation arrives as a piece of its own with its own time.
            # Given a stamp it would be read back as a word and the line
            # would say "cheeks ?", so it joins the word it belongs to.
            if len(line) > 1 and not _WORD.match(text):
                line[-1] += text
            else:
                line.append(f"<{_stamp(int(start))}> {text}")
        if len(line) > 1:
            # Where the singing stops, which is the last word's end — not the
            # line's own duration, which NetEase runs on to the next line and
            # which would hide every instrumental break in the song.
            last = pieces[-1]
            line.append(f"<{_stamp(int(last[0]) + int(last[1]))}>")
            out.append(" ".join(line))
    return "\n".join(out) + ("\n" if out else "")


def _fits(song: dict, title: str, artist: str, duration: float) -> bool:
    """Is this really the track asked for?"""
    wanted, found = _words(title), _words(song.get("name") or "")
    if not wanted or not (wanted & found):
        return False
    if len(wanted - found) > len(wanted) / 2:
        return False                   # barely half the title in common
    singers = " ".join(one.get("name") or "" for one in (song.get("artists") or []))
    if artist and _words(artist) and not (_words(artist) & _words(singers)):
        return False
    theirs = float(song.get("duration") or 0) / 1000.0
    if duration and theirs and abs(theirs - duration) > CLOSE_ENOUGH:
        return False
    return True


def find(artist: str, title: str, duration: float = 0.0) -> dict | None:
    """The NetEase song that is this track, or None if none of them is."""
    term = " ".join(f"{title} {artist}".split())
    if not term:
        return None
    answer = _ask(f"{SEARCH}?type=1&limit={LIMIT}&s=" + urllib.parse.quote(term))
    if not answer:
        return None
    for song in ((answer.get("result") or {}).get("songs") or []):
        if _fits(song, title, artist, duration):
            return song
    return None


def fetch(artist: str, title: str, duration: float = 0.0) -> str:
    """Word-timed lyrics for one track as enhanced LRC, or "" if there are none."""
    song = find(artist, title, duration)
    if song is None:
        return ""
    answer = _ask(f"{LYRIC}?id={song['id']}&lv=1&kv=1&tv=1&yv=1")
    if not answer:
        return ""
    return to_lrc((answer.get("yrc") or {}).get("lyric") or "")
