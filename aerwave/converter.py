"""Audio conversion with ffmpeg, and the format presets Aerwave offers."""

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from . import media

# key -> (label, container extension, ffmpeg arguments builder)
FORMATS = {
    "copy":  ("Keep the original file", None, None),
    "opus":  ("Opus", ".opus", "libopus"),
    "mp3":   ("MP3", ".mp3", "libmp3lame"),
    "aac":   ("AAC (m4a)", ".m4a", "aac"),
    "flac":  ("FLAC", ".flac", "flac"),
    "vorbis": ("Ogg Vorbis", ".ogg", "libvorbis"),
}

QUALITIES = {
    "opus": ["96k", "128k", "160k", "192k", "256k"],
    "mp3": ["128k", "192k", "256k", "320k"],
    "aac": ["128k", "192k", "256k"],
    "vorbis": ["128k", "192k", "256k"],
    "flac": ["default"],
    "copy": ["default"],
}

DEFAULT_QUALITY = {"opus": "128k", "mp3": "256k", "aac": "192k",
                   "vorbis": "192k", "flac": "default", "copy": "default"}


def extension_for(fmt: str, source: str) -> str:
    label = FORMATS.get(fmt)
    if not label or label[1] is None:
        return Path(source).suffix
    return label[1]


# ffmpeg's Ogg muxer refuses a picture stream outright ("Unsupported codec id"),
# so Opus and Vorbis cannot carry embedded art. For those, art travels as a
# cover file beside the tracks, which is what phone players read anyway.
ART_EMBEDDABLE = {"mp3", "aac", "flac"}


def can_embed_art(fmt: str) -> bool:
    return fmt in ART_EMBEDDABLE


def build_command(source: str, target: str, fmt: str, quality: str,
                  art: str | None = None, lyrics: str | None = None) -> list[str]:
    """The ffmpeg call that turns `source` into `target`.

    `art` is a cover image to embed (or None to keep whatever the source
    carries); `lyrics` is LRC text to write into the file's lyrics tag.
    """
    codec = FORMATS[fmt][2]
    command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", source]
    embed_art = can_embed_art(fmt)
    if embed_art and art:
        command += ["-i", art]
    command += ["-map", "0:a:0", "-map_metadata", "0"]
    if not embed_art:
        command += ["-vn"]
    elif art:
        command += ["-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    else:
        # Carry the source's own picture when it has one; ? makes it optional.
        command += ["-map", "0:v?", "-c:v", "copy", "-disposition:v", "attached_pic"]
    if fmt == "flac":
        command += ["-c:a", "flac", "-compression_level", "5"]
    else:
        command += ["-c:a", codec, "-b:a", quality]
    if lyrics:
        command += ["-metadata", f"LYRICS={lyrics}"]
    if fmt == "mp3":
        command += ["-id3v2_version", "3"]
    command.append(target)
    return command


def convert(source: str, target: str, fmt: str, quality: str, timeout: int = 900,
            art: str | None = None, lyrics: str | None = None) -> str:
    """Convert one file. Returns "" on success, or a message describing the failure."""
    target_path = Path(target)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"could not create {target_path.parent}: {exc}"
    # ffmpeg picks the container from the extension, so the temporary name has
    # to keep it: a plain ".part" suffix makes it refuse the output outright.
    partial = target_path.with_name(f".{target_path.stem}.aerwavepart{target_path.suffix}")
    try:
        result = media.run(build_command(source, str(partial), fmt, quality,
                                         art, lyrics), timeout=timeout)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        return f"could not run ffmpeg: {exc}"
    if result is None:                        # nothing ran: we are shutting down
        partial.unlink(missing_ok=True)
        return "stopped"
    if result.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        message = (result.stderr or "").strip().splitlines()
        partial.unlink(missing_ok=True)
        return message[-1][:160] if message else "ffmpeg failed"
    try:
        partial.replace(target_path)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        return f"could not write {target_path.name}: {exc}"
    return ""


class ConvertWorker(QObject):
    """Converts a batch of files into a folder, on a worker thread."""

    progress = Signal(int, int, str)
    finished = Signal(int, int)          # converted, failed

    def __init__(self, tracks: list, destination: str, fmt: str, quality: str, parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.destination = Path(destination)
        self.fmt = fmt
        self.quality = quality
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        done = failed = 0
        for index, track in enumerate(self.tracks, 1):
            if self._cancel:
                break
            source = track["path"]
            name = track.get("title") or Path(source).stem
            self.progress.emit(index, len(self.tracks), name)
            stem = "".join(c for c in name if c.isalnum() or c in " -_()&',.")[:100].strip()
            number = int(track.get("track_no") or 0)
            filename = f"{number:02d} {stem}" if number else stem
            target = self.destination / f"{filename}{extension_for(self.fmt, source)}"
            if self.fmt == "copy":
                try:
                    self.destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    done += 1
                except OSError:
                    failed += 1
                continue
            if convert(source, str(target), self.fmt, self.quality):
                failed += 1
            else:
                done += 1
        self.finished.emit(done, failed)


class ConvertDialog(QDialog):
    """Pick a format and a folder, then convert the chosen songs."""

    def __init__(self, tracks: list, parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.setWindowTitle("Convert audio")
        self.setMinimumWidth(520)
        self._thread: QThread | None = None
        self._worker: ConvertWorker | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(12)
        heading = QLabel(f"Convert {len(tracks)} song{'s' if len(tracks) != 1 else ''}")
        heading.setObjectName("EmptyTitle")
        column.addWidget(heading)

        form = QFormLayout()
        form.setSpacing(9)
        self.fmt = QComboBox()
        for key, (label, extension, _codec) in FORMATS.items():
            if key == "copy":
                continue
            self.fmt.addItem(label, key)
        self.fmt.currentIndexChanged.connect(self._format_changed)
        form.addRow("Format", self.fmt)
        self.quality = QComboBox()
        form.addRow("Quality", self.quality)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.destination = QLineEdit(str(Path.home() / "Music" / "Converted"))
        row.addWidget(self.destination, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Save into", holder)
        column.addLayout(form)

        self.progress = QProgressBar()
        self.progress.hide()
        column.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("HintLabel")
        column.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.start_button = QPushButton("Convert")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        buttons.addWidget(self.start_button)
        column.addLayout(buttons)
        self._format_changed()

    def _format_changed(self) -> None:
        fmt = self.fmt.currentData() or "opus"
        self.quality.clear()
        for value in QUALITIES.get(fmt, ["default"]):
            self.quality.addItem(value)
        index = self.quality.findText(DEFAULT_QUALITY.get(fmt, "default"))
        if index >= 0:
            self.quality.setCurrentIndex(index)
        self.quality.setEnabled(fmt != "flac")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save converted files into",
                                                  self.destination.text())
        if folder:
            self.destination.setText(folder)

    def _start(self) -> None:
        destination = self.destination.text().strip()
        if not destination:
            self.status.setText("Choose a folder first.")
            return
        self.start_button.setEnabled(False)
        self.progress.setRange(0, len(self.tracks))
        self.progress.show()
        self._thread = QThread()
        self._worker = ConvertWorker(self.tracks, destination, self.fmt.currentData(),
                                     self.quality.currentText())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(done)
        self.status.setText(f"{done}/{total} · {name}")

    def _on_finished(self, done: int, failed: int) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(4000)
            self._thread = None
        self._worker = None
        self.start_button.setEnabled(True)
        self.status.setText(f"Converted {done}"
                            + (f", {failed} failed" if failed else "")
                            + f" into {self.destination.text()}")

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        super().reject()
