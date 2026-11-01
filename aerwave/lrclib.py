"""LRCLIB: the open lyrics database Aerwave searches when a track has no lyrics.

Two endpoints matter. ``/api/get`` is the exact-match lookup — it needs the
artist, the track name, the album and the duration, and answers with one record
or nothing. ``/api/search`` is fuzzier and answers with a list, so it catches
the tracks whose tags don't quite line up. Records carry the words twice:
``syncedLyrics`` as an LRC with timestamps, ``plainLyrics`` without.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
# LRCLIB asks clients to identify themselves, so a real name beats a browser
# string: it keeps this player out of the generic-crawler pile.
USER_AGENT = "Aerwave/3.0 (desktop music player)"
TIMEOUT = 8        # in line with every other service Aerwave asks


def _get(endpoint: str, params: dict) -> tuple[object, bool]:
    """One GET against the API as (data, reached).

    `reached` is False only when the network itself failed; an HTTP error such
    as a 404 means the server answered and there is simply nothing there.
    """
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace")), True
    except urllib.error.HTTPError:
        return None, True
    except (OSError, ValueError):
        return None, False


# A stamp in front of a word rather than a line: the "enhanced" LRC that
# lets the words light up one at a time.
_WORD_TIMED = re.compile(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>")


def worded(lyric: str) -> bool:
    return bool(_WORD_TIMED.search(lyric or ""))


def _score(record: dict, duration: float) -> tuple[int, int, float]:
    """Sort key: word timings first, then line timings, then the best length.

    LRCLIB carries no word timings of its own today, but the field is free
    text that anyone may upload, so some records have them — and if one does,
    it is the one worth having.
    """
    lyric = record.get("syncedLyrics") or ""
    return (0 if worded(lyric) else 1,
            0 if lyric else 1,
            _length_gap(record, duration))


def _length_gap(record: dict, duration: float) -> float:
    if not duration:
        return 0.0
    try:
        return abs(float(record.get("duration") or 0) - duration)
    except (TypeError, ValueError):
        return 9999.0


def best_match(records: list, duration: float) -> dict | None:
    """The record worth keeping: with lyrics, timed as finely as possible."""
    usable = [r for r in records if isinstance(r, dict)
              and (r.get("syncedLyrics") or r.get("plainLyrics"))]
    if not usable:
        return None
    return sorted(usable, key=lambda r: _score(r, duration))[0]


def fetch(artist: str, title: str, album: str = "", duration: float = 0.0,
          ) -> tuple[str, str] | None:
    """Lyrics for one track as (synced LRC, plain text); either may be "".

    None means LRCLIB could not be reached at all — different from "not found",
    because an offline run must not be remembered as one.
    """
    if not artist.strip() or not title.strip():
        return "", ""
    params = {"artist_name": artist, "track_name": title}
    if album.strip():
        params["album_name"] = album
    if duration:
        params["duration"] = str(int(round(duration)))

    reached = False
    record, ok = _get("get", params)
    reached |= ok
    records = [record] if isinstance(record, dict) else []
    if not records:
        found, ok = _get("search", params)
        reached |= ok
        records = found if isinstance(found, list) else []
        if not records:
            found, ok = _get("search", {"q": f"{artist} {title}"})
            reached |= ok
            records = found if isinstance(found, list) else []
    if not reached:
        return None

    best = best_match(records, duration)
    if best is None:
        return "", ""
    return (best.get("syncedLyrics") or "", best.get("plainLyrics") or "")
