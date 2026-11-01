"""Lyrics: finding them, reading the timings, and following along.

Two places carry them, and Aeris reads both:

* an **.lrc file** beside the track, which is the usual way synced lyrics are
  distributed;
* a **lyrics tag inside the file**. For MP3 and M4A that is the ID3/iTunes
  lyrics frame; for FLAC, Opus and Ogg it is a ``LYRICS`` Vorbis comment, which
  ffprobe reports on the stream rather than the container.

An LRC is just lines stamped with ``[mm:ss.xx]``. Anything without stamps is
still shown, just not followed.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import media

SIDECAR_SUFFIXES = (".lrc", ".LRC")
TAG_KEYS = ("lyrics", "unsyncedlyrics", "syncedlyrics", "lyrics-eng", "©lyr", "uslt")

_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# The enhanced ("A2") form puts a stamp in front of every word:
#     [00:17.20] <00:17.20> Just <00:17.75> a <00:18.10> little
_WORD_STAMP = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")
_ID_TAG = re.compile(r"^\s*\[(ar|ti|al|au|by|re|ve|length|offset|tool|encoder):([^\]]*)\]\s*$",
                     re.IGNORECASE)


def _seconds(minutes: str, seconds: str, fraction: str | None) -> float:
    """A stamp's time. ".5" is half a second, and so are ".50" and ".500"."""
    fraction = fraction or "0"
    return int(minutes) * 60 + int(seconds) + int(fraction) / 10 ** len(fraction)


class Line(tuple):
    """One line: when it starts, what it says, and when each word starts.

    A plain ``(start, text)`` pair to anything that unpacks it — which is how
    every reader of a line already treats one — with the word timings riding
    along for the two places that can show them.
    """

    def __new__(cls, start, text: str, words=(), end=None, rest=None):
        line = super().__new__(cls, (start, text))
        line.words = tuple(words)       # [(start, text)] in order
        line.end = end                  # when the singing stops, if known
        line.rest = rest                # (from, to) when this is an instrumental
        return line

    @property
    def start(self):
        return self[0]

    @property
    def text(self) -> str:
        return self[1]

    def sung_through(self, position: float) -> int:
        """How many characters of `text` have been sung by `position`.

        The text is the words joined by single spaces — that is how it was
        built — so a count of words is a count of characters.
        """
        index = self.word_at(position)
        if index < 0:
            return 0
        return len(" ".join(text for _start, text in self.words[:index + 1]))

    def word_at(self, position: float) -> int:
        """Which word is being sung at `position`. -1 before the first."""
        found = -1
        for index, (start, _text) in enumerate(self.words):
            if start <= position + 0.02:
                found = index
            else:
                break
        return found


@dataclass
class Lyrics:
    """Lines of a song, with timings when the source had them."""

    lines: list = field(default_factory=list)   # [Line]
    synced: bool = False
    source: str = ""                            # "file" | "tag" | ""

    def __bool__(self) -> bool:
        return bool(self.lines)

    @property
    def worded(self) -> bool:
        """Whether the source timed the words, not just the lines."""
        return any(getattr(line, "words", ()) for line in self.lines)

    @property
    def quality(self) -> int:
        """How good a set of lyrics this is: 2 worded, 1 timed, 0 plain."""
        if not self.lines:
            return -1
        return 2 if self.worded else 1 if self.synced else 0

    def index_at(self, position: float) -> int:
        """Which line is playing at `position`. -1 before the first one."""
        if not self.synced:
            return -1
        found = -1
        for index, (start, _text) in enumerate(self.lines):
            if start is not None and start <= position + 0.05:
                found = index
            else:
                break
        return found


def parse_lrc(text: str) -> Lyrics:
    """Turn LRC text into timed lines.

    Handles the awkward parts of the format: several stamps on one line for a
    repeated chorus, the ``[offset:]`` correction, and files that are really
    just plain lyrics with no timings at all.
    """
    offset = 0.0
    timed: list[tuple[float, str]] = []
    plain: list[str] = []

    for raw in text.splitlines():
        tag = _ID_TAG.match(raw)
        if tag:
            if tag.group(1).lower() == "offset":
                try:
                    # Positive offset means the lyrics are early, so shift back.
                    offset = -float(tag.group(2).strip()) / 1000.0
                except ValueError:
                    offset = 0.0
            continue

        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            stripped = raw.strip()
            if stripped:
                plain.append(stripped)
            continue

        rest = raw[stamps[-1].end():]
        words, ends = _parse_words(rest, offset)
        spoken = (" ".join(text for _start, text in words) if words
                  else _WORD_STAMP.sub(" ", rest))
        spoken = re.sub(r"\s{2,}", " ", spoken).strip()
        for stamp in stamps:
            start = _seconds(stamp.group(1), stamp.group(2), stamp.group(3))
            timed.append(Line(max(0.0, start + offset), spoken, words, end=ends))

    if timed:
        timed.sort(key=lambda line: line.start)
        return Lyrics(lines=timed, synced=True)
    if plain:
        return Lyrics(lines=[Line(None, line) for line in plain], synced=False)
    return Lyrics()


REST = 5.0                 # a gap this long is an instrumental, not a breath
WORD_TIME = 0.55           # how long a word takes to sing, when nothing says
LEAST_SUNG = 3.0           # no line is given less than this
MOST_SUNG = 12.0           # nor more, however many words it has
LINGER = 0.35              # and a line before a long break holds this much of it


def _sung_for(line) -> float:
    """How long a line probably takes to sing, from its words alone."""
    words = len((line[1] or "").split())
    return max(LEAST_SUNG, min(words * WORD_TIME, MOST_SUNG))


def _stops_at(line, starts: float | None) -> float | None:
    """When the singing on `line` stops: known if the file says, else guessed.

    Only LRCLIB-style files need the guess. They stamp where a line begins
    and nothing else, so the end is unknown — and taking the start as the end,
    which is what this used to do, put the break at the same instant as the
    line, so a lone line between two instrumentals was never shown: the dots
    came up in its place.

    The guess is the word count at a singing pace, and a line sitting in front
    of a long break is allowed to linger over part of it, because a lyric held
    a few seconds too long reads far better than words that vanish the moment
    they arrive. `starts` is when the next line comes in, or None if this is
    the last.
    """
    if line.end is not None:
        return line.end
    if line.start is None:
        return None
    if line.words:
        # Worded, but with no closing stamp: the last word is still sung.
        return line.words[-1][0] + WORD_TIME
    held = _sung_for(line)
    if starts is not None:
        held = max(held, min(LINGER * (starts - line.start), MOST_SUNG))
    return line.start + held


def with_rests(lines: list) -> list:
    """The same lines, with the instrumental breaks marked between them.

    A lyric view that sits on the last line sung for half a minute looks
    broken. Marking the gaps lets the view show that nothing is being sung
    and how long that lasts.
    """
    out = []
    for index, line in enumerate(lines):
        if index == 0 and line.start and line.start >= REST:
            out.append(Line(0.0, "", rest=(0.0, line.start)))
        out.append(line)
        if index + 1 >= len(lines):
            break
        stops = _stops_at(line, lines[index + 1].start)
        starts = lines[index + 1].start
        if stops is not None and starts is not None and starts - stops >= REST:
            out.append(Line(stops, "", rest=(stops, starts)))
    return out


def _parse_words(rest: str, offset: float) -> tuple:
    """The word stamps in one line, as ([(start, word)], end) in order.

    Empty when the line has none, which is the ordinary LRC case. A trailing
    stamp with no word after it is where the singing stops, which is what
    tells an instrumental break apart from a pause for breath.
    """
    stamps = list(_WORD_STAMP.finditer(rest))
    if not stamps:
        return [], None
    words, end = [], None
    for index, stamp in enumerate(stamps):
        stops = stamps[index + 1].start() if index + 1 < len(stamps) else len(rest)
        text = rest[stamp.end():stops].strip()
        start = max(0.0, _seconds(stamp.group(1), stamp.group(2), stamp.group(3)) + offset)
        if not text:
            end = start                 # the end-of-line marker, or a double stamp
            continue
        words.append((start, text))
    return words, end


def sidecar_for(track_path: str) -> Path | None:
    """An .lrc sitting next to the track."""
    source = Path(track_path)
    for suffix in SIDECAR_SUFFIXES:
        candidate = source.with_suffix(suffix)
        if candidate.is_file() and candidate.stat().st_size:
            return candidate
    return None


def embedded_text(track_path: str) -> str:
    """A lyrics tag inside the file, from the container or the audio stream."""
    try:
        result = media.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", track_path], timeout=30)
        if result is None:                    # nothing ran: we are shutting down
            return ""
        data = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    pools = [data.get("format", {}).get("tags") or {}]
    for stream in data.get("streams", []):
        pools.append(stream.get("tags") or {})
    for pool in pools:
        lowered = {str(k).lower(): v for k, v in pool.items()}
        for key in TAG_KEYS:
            value = lowered.get(key)
            if value and str(value).strip():
                return str(value)
    return ""


def raw_text(track_path: str) -> str:
    """The lyrics exactly as written, for putting into another file."""
    sidecar = sidecar_for(track_path)
    if sidecar is not None:
        try:
            text = sidecar.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text
        except OSError:
            pass
    return embedded_text(track_path)


def find_lyrics(track_path: str) -> Lyrics:
    """The best lyrics for one track, from beside it or inside it.

    Best, not first. A file beside the track is the usual home for the good
    ones and is still preferred at equal standing, but lyrics timed word by
    word beat lyrics timed line by line wherever they turn up, and either
    beats a wall of plain text.
    """
    found = []
    sidecar = sidecar_for(track_path)
    if sidecar is not None:
        try:
            parsed = parse_lrc(sidecar.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            parsed = Lyrics()
        if parsed:
            parsed.source = "file"
            found.append(parsed)
    text = embedded_text(track_path)
    if text:
        parsed = parse_lrc(text)
        if parsed:
            parsed.source = "tag"
            found.append(parsed)
    if not found:
        return Lyrics()
    return max(found, key=lambda one: one.quality)   # a tie keeps the sidecar


def rank(text: str) -> int:
    """How good some lyrics text is: 2 worded, 1 timed, 0 plain, -1 nothing."""
    return parse_lrc(text or "").quality
