"""Musixmatch, through its official API and a key of your own.

Musixmatch has the most complete set of timed lyrics there is, line by line
for most songs and word by word ("richsync") for many. The catch is the plan.
A free developer key gets 30% of each song's words and no timings at all,
which is no use to a lyrics view and is never saved; timed lyrics come with
the paid plans. So this asks for the best the key allows — richsync, then
line-timed subtitles — and when the plan does not cover something it says so
once and stops asking, rather than spending a request on every song in the
library to be told the same thing.

There used to be a keyless way in, through the token Musixmatch's desktop app
fetched for itself. It now hands out a token of zeros that returns unrelated
songs, and the only other route is copying the request signing their phone
app hides, which Aerwave does not do.

Like the other providers, a match has to look like the track before it is
used: a word-timed lyric for the wrong song is worse than none.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.musixmatch.com/ws/1.1/"
AGENT = "Aerwave/3.0 (desktop music player)"
TIMEOUT = 10
CLOSE_ENOUGH = 8.0             # seconds of difference still counted as the same track

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Lines Musixmatch adds to lyrics on the cheaper plans; they are not the song.
_NOTICE = re.compile(r"\*{3,}.*\*{3,}|not for commercial use", re.IGNORECASE)


class PlanError(Exception):
    """The key is wrong, spent, or its plan does not include what was asked.

    Raised rather than returned, so a library-wide pass stops asking after the
    first one instead of making the same refused request for every song.
    """


def _ask(method: str, key: str, **params) -> dict | None:
    """One call. The body on success, None when there is simply nothing."""
    query = urllib.parse.urlencode({**params, "apikey": key, "format": "json"})
    request = urllib.request.Request(API + method + "?" + query,
                                     headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            message = json.load(response).get("message", {})
    except (urllib.error.URLError, OSError, ValueError):
        return None                   # offline or garbled: try again another day
    status = int(message.get("header", {}).get("status_code") or 0)
    if status == 200:
        body = message.get("body")
        return body if isinstance(body, dict) else None
    if status == 401:
        raise PlanError("Musixmatch did not accept the API key")
    if status == 402:
        raise PlanError("the Musixmatch key has used up its allowance for now")
    if status == 403:
        raise PlanError("the Musixmatch plan does not include timed lyrics")
    return None                       # 404 and friends: not on Musixmatch


def _words(text: str) -> set:
    return {word.lower() for word in _WORD.findall(text or "") if len(word) > 1}


def _fits(track: dict, artist: str, title: str, duration: float) -> bool:
    """Whether Musixmatch's answer is plausibly the song being played."""
    wanted = _words(title)
    if wanted and not wanted & _words(track.get("track_name", "")):
        return False
    who = _words(artist)
    if who and not who & _words(track.get("artist_name", "")):
        return False
    length = float(track.get("track_length") or 0)
    return not (duration and length and abs(length - duration) > CLOSE_ENOUGH)


def _stamp(seconds: float) -> str:
    whole, hundredths = divmod(max(0, int(round(seconds * 100))), 100)
    return f"{whole // 60:02d}:{whole % 60:02d}.{hundredths:02d}"


def richsync_to_lrc(body: str) -> str:
    """Musixmatch's word timings as enhanced LRC.

    Each line arrives as ``{"ts": start, "te": end, "l": [{"c": text, "o":
    offset}, …]}`` where the pieces are words and the spaces between them,
    each timed from the start of the line. Spaces join words up; every word
    gets its own stamp; the line closes with its end, which is what lets the
    lyrics view tell an instrumental break from a breath.
    """
    try:
        lines = json.loads(body)
    except (TypeError, ValueError):
        return ""
    out = []
    for line in lines if isinstance(lines, list) else []:
        start = float(line.get("ts") or 0)
        end = float(line.get("te") or 0)
        words: list[list] = []         # [time, text]
        gap = True
        for piece in line.get("l") or []:
            text = str(piece.get("c") or "")
            when = start + float(piece.get("o") or 0)
            if not text.strip():
                gap = True
                continue
            if gap or not words:
                words.append([when, text])
            else:
                words[-1][1] += text   # punctuation, or a word sent in parts
            gap = False
        if not words:
            continue
        pieces = [f"[{_stamp(words[0][0])}]"]
        pieces += [f"<{_stamp(when)}> {text}" for when, text in words]
        if end > words[-1][0]:
            pieces.append(f"<{_stamp(end)}>")
        out.append(" ".join(pieces))
    return "\n".join(out) + ("\n" if out else "")


def _clean(subtitles: str) -> str:
    return "\n".join(line for line in (subtitles or "").splitlines()
                     if not _NOTICE.search(line)).strip()


def fetch(key: str, artist: str, title: str, duration: float = 0.0) -> str:
    """The best timed lyrics the key can get for this track, or "".

    Word-timed first, then line-timed. Untimed words are not returned: on a
    free key they are cut to a third of the song, and LRCLIB has them whole.
    Raises PlanError when the key itself is the problem.
    """
    if not key or not title:
        return ""
    params = {"q_track": title}
    if artist:
        params["q_artist"] = artist
    if duration:
        params["q_duration"] = int(duration)
    found = _ask("matcher.track.get", key, **params)
    track = (found or {}).get("track") or {}
    if not track or not _fits(track, artist, title, duration):
        return ""
    ident = {"commontrack_id": track["commontrack_id"]} if track.get("commontrack_id") \
        else {"track_id": track.get("track_id")}
    if track.get("has_richsync"):
        rich = _ask("track.richsync.get", key, **ident)
        lrc = richsync_to_lrc(((rich or {}).get("richsync") or {}).get("richsync_body", ""))
        if lrc:
            return lrc
    if track.get("has_subtitles"):
        sub = _ask("track.subtitle.get", key, subtitle_format="lrc", **ident)
        lrc = _clean(((sub or {}).get("subtitle") or {}).get("subtitle_body", ""))
        if lrc:
            return lrc + "\n"
    return ""
