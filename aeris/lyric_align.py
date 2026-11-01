"""Timing a song's own lyrics against the audio, word by word, offline.

Word-timed lyrics are rare: NetEase has them for a fraction of what it knows
and LRCLIB has none at all. But the words themselves are easy to come by, and
a speech model can say when each one is sung — so the two halves can be put
together here, on this machine, for any song in the library.

What this does is alignment, not transcription. The words stay exactly as the
lyrics file has them, spelling, punctuation and line breaks included; all that
is taken from the model is *when*. The model's own transcript of a song is a
poor lyric sheet — it mishears, it skips, it punctuates oddly — so it is used
only as a list of times to attach to words we already trust, matched up with
difflib. Where a run of lyric words has no match (a mumbled line, a harmony)
their times are spread evenly between the words on either side that did match.

Two engines, whichever is to hand:

* **whisper.cpp** — the ``whisper-cli`` binary with a ggml model. Asked first:
  it is far quicker on a CPU than anything in Python, it holds no interpreter
  memory, and a whole album can go through it in the time the Python engine
  takes to load. ``-ml 1`` makes it cut the transcript at every word, so each
  segment it reports *is* a word with a time.
* **faster-whisper** — the engine Aeris already offers for video subtitles,
  which reports word times of its own when asked. Used when whisper.cpp is
  not installed, so this works out of the box for anyone who has ever made
  subtitles.

Nothing here touches the network, and nothing is installed behind your back:
with neither engine present the feature simply says so.
"""

import json
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from . import lyrics
from .paths import DATA_DIR

# Where a ggml model may be sitting. The first that exists wins.
MODEL_DIRS = [
    DATA_DIR / "whisper-cpp",
    Path.home() / ".cache" / "whisper.cpp",
    Path.home() / ".local" / "share" / "whisper.cpp" / "models",
    Path("/usr/share/whisper.cpp/models"),
    Path("/usr/share/whisper.cpp"),
]
# In the order we would rather have them: better before faster, because a
# song is three minutes of audio and this runs once.
MODEL_ORDER = ("large-v3", "large", "medium", "small", "base", "tiny")
BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp", "whispercpp")

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+", re.UNICODE)


def _normal(word: str) -> str:
    """A word as the matcher sees it: letters, lowered, no punctuation."""
    found = _WORD.findall(word.casefold())
    return "".join(found)


# ------------------------------------------------------------------ engines


def whisper_cpp() -> tuple[str, Path] | None:
    """The whisper.cpp binary and a model for it, if both are installed."""
    binary = next((shutil.which(name) for name in BINARIES
                   if shutil.which(name)), None)
    if binary is None:
        return None
    found: dict[str, Path] = {}
    for folder in MODEL_DIRS:
        try:
            entries = sorted(folder.glob("ggml-*.bin"))
        except OSError:
            continue
        for entry in entries:
            name = entry.stem[len("ggml-"):]
            found.setdefault(name.removesuffix(".en"), entry)
    for size in MODEL_ORDER:
        if size in found:
            return binary, found[size]
    return (binary, next(iter(found.values()))) if found else None


def engine() -> str:
    """Which engine would be used: "whisper.cpp", "faster-whisper" or ""."""
    if whisper_cpp() is not None:
        return "whisper.cpp"
    import importlib.util                       # noqa: PLC0415  (optional)
    if importlib.util.find_spec("faster_whisper") is not None:
        return "faster-whisper"
    return ""


def _wav(track_path: str, folder: Path) -> Path | None:
    """The track as the 16 kHz mono WAV every Whisper build wants."""
    out = folder / "song.wav"
    try:
        done = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", track_path,
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out)],
            capture_output=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out if done.returncode == 0 and out.is_file() else None


def _heard_by_cpp(binary: str, model: Path, wav: Path, folder: Path,
                  language: str = "") -> list[tuple[float, float, str]]:
    """Words and their times from whisper.cpp."""
    base = folder / "heard"
    command = [binary, "-m", str(model), "-f", str(wav), "-oj", "-of", str(base),
               "-ml", "1", "-np", "-nt"]
    if language:
        command += ["-l", language]
    try:
        subprocess.run(command, capture_output=True, timeout=3600, check=True)
        payload = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    words = []
    for piece in payload.get("transcription", []):
        text = (piece.get("text") or "").strip()
        offsets = piece.get("offsets") or {}
        if not text or "from" not in offsets:
            continue
        words.append((offsets["from"] / 1000.0,
                      offsets.get("to", offsets["from"]) / 1000.0, text))
    return words


def _heard_by_python(wav: Path, model_size: str,
                     language: str = "") -> list[tuple[float, float, str]]:
    """The same, from faster-whisper."""
    try:
        from faster_whisper import WhisperModel   # noqa: PLC0415  (optional)
    except ImportError:
        return []
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(wav), word_timestamps=True,
                                       language=language or None,
                                       vad_filter=False)
    words = []
    for segment in segments:
        for word in segment.words or []:
            text = (word.word or "").strip()
            if text:
                words.append((float(word.start), float(word.end), text))
    return words


def heard(track_path: str, model_size: str = "base",
          language: str = "") -> list[tuple[float, float, str]]:
    """Every word the model hears in the track, with its times."""
    with tempfile.TemporaryDirectory(prefix="aeris-align-") as scratch:
        folder = Path(scratch)
        wav = _wav(track_path, folder)
        if wav is None:
            return []
        found = whisper_cpp()
        if found is not None:
            binary, model = found
            words = _heard_by_cpp(binary, model, wav, folder, language)
            if words:
                return words
        return _heard_by_python(wav, model_size, language)


# ---------------------------------------------------------------- aligning


def align(lines: list[str], words: list[tuple[float, float, str]]) -> list[list]:
    """Put times to the words of `lines`, using the words the model heard.

    Returns one list per line of ``[(start, end, word)]``, lines the matcher
    could place nothing in left empty. The words are the lyrics' own.
    """
    own: list[tuple[int, int, str]] = []       # (line, place in line, word)
    laid_out: list[list[str]] = []
    for number, text in enumerate(lines):
        pieces = (text or "").split()
        laid_out.append(pieces)
        for place, piece in enumerate(pieces):
            own.append((number, place, piece))

    mine = [_normal(word) for _line, _place, word in own]
    theirs = [_normal(text) for _start, _end, text in words]
    times: list[tuple[float, float] | None] = [None] * len(own)
    matcher = SequenceMatcher(a=mine, b=theirs, autojunk=False)
    for here, there, length in matcher.get_matching_blocks():
        for step in range(length):
            start, end, _text = words[there + step]
            times[here + step] = (start, end)

    _fill_gaps(times)
    out: list[list] = [[] for _ in lines]
    for (number, _place, word), when in zip(own, times):
        if when is not None:
            out[number].append((when[0], when[1], word))
    for number, placed in enumerate(out):
        placed.sort(key=lambda item: item[0])
        if placed and len(placed) < len(laid_out[number]):
            pass                      # a partly matched line keeps what it got
    return out


def _fill_gaps(times: list) -> None:
    """Spread times across runs of words the matcher could not place.

    A word between two placed ones gets its share of the time between them;
    words before the first or after the last placed word are left alone,
    because there is nothing to interpolate from and a guess at the very
    start of a song is the one most likely to be noticed.
    """
    placed = [index for index, when in enumerate(times) if when is not None]
    for left, right in zip(placed, placed[1:]):
        missing = right - left - 1
        if missing <= 0:
            continue
        begins, ends = times[left][1], times[right][0]
        if ends <= begins:
            continue
        step = (ends - begins) / (missing + 1)
        for number in range(missing):
            at = begins + step * number
            times[left + 1 + number] = (at, at + step)


def _stamp(seconds: float) -> str:
    whole, hundredths = divmod(max(0, int(round(seconds * 100))), 100)
    return f"{whole // 60:02d}:{whole % 60:02d}.{hundredths:02d}"


def to_lrc(placed: list[list]) -> str:
    """Enhanced LRC from aligned lines: a stamp on the line, one per word,
    and a closing stamp where the singing stops — which is what tells an
    instrumental break from a pause for breath."""
    out = []
    for words in placed:
        if not words:
            continue
        pieces = [f"[{_stamp(words[0][0])}]"]
        for start, _end, word in words:
            pieces.append(f"<{_stamp(start)}> {word}")
        pieces.append(f"<{_stamp(words[-1][1])}>")
        out.append(" ".join(pieces))
    return "\n".join(out) + ("\n" if out else "")


def lines_of(text: str) -> list[str]:
    """The lyric lines of an .lrc or a plain lyric sheet, words only."""
    found = lyrics.parse_lrc(text)
    return [line[1] for line in found.lines if (line[1] or "").strip()]


def grouped(words: list[tuple[float, float, str]], gap: float = 1.2,
            most: int = 9) -> list[list]:
    """Heard words made into lines, for a song with no lyrics of its own.

    Broken where the singing pauses, or after `most` words, so the view has
    lines rather than one endless paragraph.
    """
    out: list[list] = []
    for start, end, text in words:
        if (not out or len(out[-1]) >= most
                or start - out[-1][-1][1] >= gap):
            out.append([])
        out[-1].append((start, end, text))
    return out


def time_lyrics(track_path: str, text: str = "", model_size: str = "base",
                language: str = "") -> str:
    """Enhanced LRC for one track: its own words, timed against its audio.

    `text` is the lyric sheet to time — an .lrc, timed or not, or plain
    words. With none, the model's own transcript is used, which is better
    than nothing for a song no lyrics site knows.
    """
    words = heard(track_path, model_size, language)
    if not words:
        return ""
    lines = lines_of(text) if text.strip() else []
    if not lines:
        return to_lrc(grouped(words))
    return to_lrc(align(lines, words))


def model_for(library) -> str:
    """The model size to use: the one the subtitles already settled on."""
    return str(library.get("whisper_model", "base") or "base")


def missing_note() -> str:
    """Why this cannot run, in a sentence, when no engine is installed."""
    return ("Timing lyrics needs a speech model on this machine. Install "
            "whisper.cpp (the whisper-cpp package, plus a ggml model in "
            f"{MODEL_DIRS[0]}) or let Aeris install faster-whisper the next "
            "time you generate subtitles for a video.")


def existing_text(track_path: str) -> str:
    """The words already known for a track: its .lrc, or its own tag."""
    sidecar = lyrics.sidecar_for(track_path)
    if sidecar is not None:
        try:
            return sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return lyrics.embedded_text(track_path)


class _Signals(QObject):
    progress = Signal(int, int, str)       # done, total, name
    finished = Signal(int, int)            # timed, left alone


class TimingJob(QRunnable):
    """Timing a handful of tracks, one after another, off the UI thread.

    Minutes of work per song on a CPU, so it reports where it is and takes
    notice of being cancelled between songs — and between songs only, because
    a model mid-sentence cannot be interrupted politely.
    """

    def __init__(self, tracks: list, model_size: str, language: str = ""):
        super().__init__()
        self.tracks = tracks
        self.model_size = model_size
        self.language = language
        self.signals = _Signals()
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from .lyrics_fetch import write_sidecar     # noqa: PLC0415  (cycle)
        timed = missed = 0
        total = len(self.tracks)
        for number, track in enumerate(self.tracks, 1):
            if self._cancel:
                break
            path = track.get("path") or ""
            name = track.get("title") or Path(path).stem
            self.signals.progress.emit(number, total, name)
            try:
                text = time_lyrics(path, existing_text(path),
                                   self.model_size, self.language)
            except Exception:                        # a model can fail oddly
                text = ""
            if text and write_sidecar(path, text):
                timed += 1
            else:
                missed += 1
        self.signals.finished.emit(timed, missed)
