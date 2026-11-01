"""Generated subtitles: speech-to-text over a video's audio.

Transcription is optional — the engine (``faster-whisper``) is not installed
with Aerwave, because it and its models weigh far more than the app does. Aerwave
offers to fetch it into its own virtualenv the first time you ask for
subtitles, and works exactly as before if you never do.

Finished captions are written as ``<name>.whisper.srt`` beside the video, so
mpv's own fuzzy matching picks them up on every later play with no bookkeeping.
When the folder is read-only they go to the data directory instead.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from . import media
from .paths import DATA_DIR

SUBTITLE_DIR = DATA_DIR / "subtitles"
SUFFIX = ".whisper.srt"

# (identifier, label, approximate download)
MODELS = [
    ("tiny", "Tiny — fastest, roughest", "75 MB"),
    ("base", "Base — quick, decent", "145 MB"),
    ("small", "Small — noticeably better", "480 MB"),
    ("medium", "Medium — better again, slower", "1.5 GB"),
    ("large-v3", "Large — best, slowest", "3.1 GB"),
]
DEFAULT_MODEL = "base"

PACKAGE = "faster-whisper"


def engine_available() -> bool:
    """Is the transcription engine installed in this interpreter?"""
    return importlib.util.find_spec("faster_whisper") is not None


def install_command() -> list[str]:
    return [sys.executable, "-m", "pip", "install", PACKAGE]


def sidecar_path(video_path: str) -> Path:
    """Where generated captions live: beside the video when that is writable."""
    source = Path(video_path)
    beside = source.with_suffix("")
    beside = beside.with_name(beside.name + SUFFIX)
    folder = source.parent
    try:
        writable = folder.is_dir() and os.access(folder, os.W_OK)
    except OSError:
        writable = False
    if writable:
        return beside
    SUBTITLE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(source).encode()).hexdigest()[:20]
    return SUBTITLE_DIR / f"{key}{SUFFIX}"


def existing_subtitle(video_path: str) -> Path | None:
    """A previously generated file for this video, if there is one."""
    for candidate in (sidecar_path(video_path),):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def path_of(video: dict) -> str:
    return video["path"]


def timestamp(seconds: float) -> str:
    """SRT clock. Rounding happens once, in milliseconds, so it cannot
    produce an impossible ':60'."""
    total = max(0, int(round(float(seconds) * 1000)))
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# Loading a model costs a few seconds, so one stays warm per size.
_MOMENT_MODELS: dict = {}


def spoken_text(segments: list) -> str:
    """The words of a transcript, without their timings."""
    return " ".join(text for text, _start in segments).strip()


def transcribe_moment(path: str, start: float, seconds: float,
                      model_size: str = DEFAULT_MODEL, language: str = "") -> list:
    """Listen to one stretch of a file: [(what was said, seconds in), ...].

    The timings matter as much as the words. A clip usually opens with a moment
    of silence — the tail of a pause, the gap after a chapter heading — and
    pairing the first words heard with the clip's own start time would place the
    reader that much behind the voice.

    Used to work out where in a book the narration has got to, so it wants a
    minute of speech turned round quickly rather than a whole file transcribed
    perfectly — hence the small beam, and the CPU, which for a clip this short
    finishes before a GPU has finished waking up.
    """
    from faster_whisper import WhisperModel          # noqa: PLC0415  (optional engine)

    clip = _slice(path, start, seconds)
    if clip is None:
        return []
    try:
        key = (model_size, "cpu")
        model = _MOMENT_MODELS.get(key)
        if model is None:
            model = _MOMENT_MODELS[key] = WhisperModel(model_size, device="cpu",
                                                       compute_type="int8")
        segments, _info = model.transcribe(
            str(clip), language=language or None, beam_size=1, vad_filter=True,
            condition_on_previous_text=False)
        return [((segment.text or "").strip(), float(segment.start or 0))
                for segment in segments if (segment.text or "").strip()]
    finally:
        clip.unlink(missing_ok=True)


def _slice(path: str, start: float, seconds: float) -> Path | None:
    """A scratch WAV of one passage, at the 16 kHz mono the model wants."""
    import tempfile
    handle, name = tempfile.mkstemp(suffix=".wav", prefix="aerwave-listen-")
    os.close(handle)
    clip = Path(name)
    try:
        result = media.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
             "-ss", f"{max(0.0, start):.2f}", "-t", f"{max(1.0, seconds):.2f}",
             "-i", path, "-vn", "-ac", "1", "-ar", "16000", str(clip)],
            timeout=180, text=False)
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is None or result.returncode != 0 \
            or not clip.exists() or clip.stat().st_size < 1024:
        clip.unlink(missing_ok=True)
        return None
    return clip


class SetupDialog(QDialog):
    """Offers to fetch the transcription engine into Aerwave's own virtualenv."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up subtitle generation")
        self.setMinimumWidth(560)
        self.installed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        heading = QLabel("Generate subtitles by listening to the audio")
        heading.setObjectName("EmptyTitle")
        layout.addWidget(heading)

        body = QLabel(
            "Aerwave can transcribe speech into subtitles for videos that have none. "
            "The engine that does it (faster-whisper) is a separate download of "
            "roughly 200 MB, plus a language model the first time you use it.\n\n"
            "Everything is installed into Aerwave's own virtualenv — nothing is added "
            "to your system, and removing Aerwave removes it too."
        )
        body.setWordWrap(True)
        body.setObjectName("EmptyBody")
        layout.addWidget(body)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setVisible(False)
        self.output.setMaximumHeight(190)
        layout.addWidget(self.output)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Not now")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.install_button = QPushButton("Download and install")
        self.install_button.setObjectName("Primary")
        self.install_button.clicked.connect(self._install)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

        self._process: QProcess | None = None

    def _install(self) -> None:
        self.install_button.setEnabled(False)
        self.install_button.setText("Installing…")
        self.output.setVisible(True)
        self.adjustSize()
        command = install_command()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._done)
        self._process.start(command[0], command[1:])

    def _read(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self.output.appendPlainText(text.rstrip())

    def _done(self, code: int, _status) -> None:
        if code == 0 and engine_available():
            self.installed = True
            self.output.appendPlainText("\nInstalled. Subtitle generation is ready.")
            self.install_button.setText("Done")
            self.install_button.setEnabled(True)
            self.install_button.clicked.disconnect()
            self.install_button.clicked.connect(self.accept)
        else:
            self.output.appendPlainText(
                f"\nInstallation failed (exit code {code}). "
                "Check your connection and try again.")
            self.install_button.setText("Try again")
            self.install_button.setEnabled(True)


class _Signals(QObject):
    started = Signal(int, str)          # video_id, title
    progress = Signal(int, float, str)  # video_id, 0..1, phase
    finished = Signal(int, str)         # video_id, subtitle path
    failed = Signal(int, str)           # video_id, message
    gpuFailed = Signal(str)             # the GPU could not be used


class _TranscribeJob(QRunnable):
    def __init__(self, video: dict, model_size: str, language: str, use_gpu: bool,
                 signals: _Signals):
        super().__init__()
        self.video = video
        self.model_size = model_size
        self.language = language or None
        self.use_gpu = use_gpu
        self.signals = signals
        self.cancelled = False
        self.setAutoDelete(True)

    def run(self) -> None:
        video_id = self.video["id"]
        path = self.video["path"]
        self.signals.started.emit(video_id, self.video.get("title") or Path(path).stem)
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(video_id, f"the transcription engine is missing: {exc}")
            return

        lines = None
        if self.use_gpu:
            try:
                self.signals.progress.emit(video_id, 0.0, "Loading the model (GPU)")
                lines = self._run_with(WhisperModel, "cuda", "float16")
            except Exception as exc:  # noqa: BLE001
                # A GPU can look present and still be unusable — a CUDA version
                # ctranslate2 was not built for, no cuDNN, no free memory. The
                # failure only shows up once decoding starts, so the CPU retry
                # lives here rather than around the model constructor.
                self.signals.gpuFailed.emit(str(exc))
                lines = None
            if self.cancelled:
                return
        if lines is None:
            try:
                self.signals.progress.emit(video_id, 0.0, "Loading the model")
                lines = self._run_with(WhisperModel, "cpu", "int8")
            except Exception as exc:  # noqa: BLE001
                self.signals.failed.emit(video_id, f"transcription failed: {exc}")
                return
        if self.cancelled:
            return

        if self.cancelled:
            return
        if not lines:
            self.signals.failed.emit(video_id, "no speech was found in this file")
            return
        target = sidecar_path(path)
        try:
            target.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            self.signals.failed.emit(video_id, f"could not write {target.name}: {exc}")
            return
        self.signals.progress.emit(video_id, 1.0, "Done")
        self.signals.finished.emit(video_id, str(target))

    def _run_with(self, WhisperModel, device: str, compute_type: str) -> list[str]:
        """Transcribe on one device, returning SRT blocks."""
        video_id = self.video["id"]
        model = WhisperModel(self.model_size, device=device, compute_type=compute_type)
        if self.cancelled:
            return []
        self.signals.progress.emit(video_id, 0.0, "Listening")
        segments, info = model.transcribe(
            path_of(self.video), language=self.language, beam_size=5, vad_filter=True,
            condition_on_previous_text=False,
        )
        duration = float(getattr(info, "duration", 0) or self.video.get("duration") or 0)
        lines: list[str] = []
        for index, segment in enumerate(segments, 1):
            if self.cancelled:
                return []
            text = (segment.text or "").strip()
            if text:
                lines.append(
                    f"{len(lines) + 1}\n"
                    f"{timestamp(segment.start)} --> {timestamp(segment.end)}\n"
                    f"{text}\n"
                )
            if duration > 0 and index % 4 == 0:
                self.signals.progress.emit(
                    video_id, min(0.99, float(segment.end) / duration), "Transcribing")
        return lines


class Transcriber(QObject):
    """Runs one transcription at a time and reports how it is going."""

    started = Signal(int, str)
    progress = Signal(int, float, str)
    finished = Signal(int, str)
    failed = Signal(int, str)
    queueChanged = Signal(int)          # how many are waiting
    gpuUnavailable = Signal(str)        # fell back to the CPU

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)     # transcription eats every core it gets
        self._signals = _Signals(self)
        self._signals.started.connect(self._on_started)
        self._signals.progress.connect(self.progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)
        self._signals.gpuFailed.connect(self._on_gpu_failed)
        self._jobs: dict[int, _TranscribeJob] = {}
        self._active: int | None = None

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    @property
    def active_id(self) -> int | None:
        return self._active

    def is_queued(self, video_id: int) -> bool:
        return video_id in self._jobs

    def start(self, video: dict) -> bool:
        video_id = video["id"]
        if video_id in self._jobs:
            return False
        model_size = str(self.library.get("whisper_model", DEFAULT_MODEL))
        language = str(self.library.get("whisper_language", "") or "")
        use_gpu = bool(self.library.get("whisper_try_gpu", True))
        job = _TranscribeJob(video, model_size, language, use_gpu, self._signals)
        self._jobs[video_id] = job
        self.queueChanged.emit(len(self._jobs))
        self._pool.start(job)
        return True

    def cancel(self, video_id: int) -> None:
        job = self._jobs.get(video_id)
        if job is not None:
            job.cancelled = True
            self._jobs.pop(video_id, None)
            if self._active == video_id:
                self._active = None
            self.queueChanged.emit(len(self._jobs))

    def cancel_all(self) -> None:
        for job in self._jobs.values():
            job.cancelled = True
        self._jobs.clear()
        self._active = None
        self._pool.clear()
        self.queueChanged.emit(0)

    def _on_gpu_failed(self, message: str) -> None:
        """Stop trying the GPU on this machine; the CPU run carries on regardless."""
        self.library.set("whisper_try_gpu", False)
        self.gpuUnavailable.emit(message)

    def _on_started(self, video_id: int, title: str) -> None:
        self._active = video_id
        self.started.emit(video_id, title)

    def _on_finished(self, video_id: int, path: str) -> None:
        self._jobs.pop(video_id, None)
        if self._active == video_id:
            self._active = None
        self.queueChanged.emit(len(self._jobs))
        self.finished.emit(video_id, path)

    def _on_failed(self, video_id: int, message: str) -> None:
        self._jobs.pop(video_id, None)
        if self._active == video_id:
            self._active = None
        self.queueChanged.emit(len(self._jobs))
        self.failed.emit(video_id, message)

    def shutdown(self) -> None:
        self.cancel_all()
        self._pool.waitForDone(2000)
