"""A headless libmpv instance for audiobook playback.

The video player renders into an OpenGL widget; audiobooks need no surface at
all, so this wraps mpv with video disabled and exposes its state as Qt signals.
"""

from PySide6.QtCore import QObject, Signal

import mpv

from .mpvwidget import use_c_numeric_locale

# Gentle levelling for quietly-recorded narration.
NORMALISE_FILTER = "dynaudnorm=f=250:g=15:p=0.9:m=8"


class AudioEngine(QObject):
    """Owns one mpv instance and turns its state into signals."""

    positionChanged = Signal(float)      # seconds into the current file
    durationChanged = Signal(float)
    pausedChanged = Signal(bool)
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)
    speedChanged = Signal(float)
    fileLoaded = Signal()
    trackEnded = Signal()
    engineError = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        use_c_numeric_locale()
        self._ended = False
        self.mpv = mpv.MPV(
            vo="null",
            video="no",
            idle="yes",
            keep_open="yes",
            osc=False,
            osd_level=0,
            input_default_bindings=False,
            input_vo_keyboard=False,
            ytdl=False,
            volume_max=200,
            audio_display="no",
            log_handler=self._on_log,
            loglevel="error",
        )
        self._connect()

    def _on_log(self, level, prefix, text):
        if level in ("fatal", "error"):
            self.engineError.emit(f"{prefix}: {text.strip()}")

    def _connect(self) -> None:
        player = self.mpv

        @player.property_observer("time-pos")
        def _time(_name, value):
            if value is not None:
                self.positionChanged.emit(float(value))

        @player.property_observer("duration")
        def _duration(_name, value):
            self.durationChanged.emit(float(value or 0))

        @player.property_observer("pause")
        def _pause(_name, value):
            self.pausedChanged.emit(bool(value))

        @player.property_observer("volume")
        def _volume(_name, value):
            if value is not None:
                self.volumeChanged.emit(float(value))

        @player.property_observer("mute")
        def _mute(_name, value):
            self.mutedChanged.emit(bool(value))

        @player.property_observer("speed")
        def _speed(_name, value):
            if value:
                self.speedChanged.emit(float(value))

        @player.property_observer("eof-reached")
        def _eof(_name, value):
            # keep-open holds the last frame, so this - not end-file - is what
            # tells us a track finished.
            if value and not self._ended:
                self._ended = True
                self.trackEnded.emit()

        @player.event_callback("file-loaded")
        def _loaded(_event):
            self._ended = False
            self.fileLoaded.emit()

        self._observers = (_time, _duration, _pause, _volume, _mute, _speed, _eof, _loaded)

    # ------------------------------------------------------------- properties

    def _get(self, name: str, default=None):
        try:
            return getattr(self.mpv, name.replace("-", "_"))
        except Exception:  # noqa: BLE001
            return default

    def _set(self, name: str, value) -> None:
        try:
            setattr(self.mpv, name.replace("-", "_"), value)
        except Exception as exc:  # noqa: BLE001
            self.engineError.emit(f"{name}: {exc}")

    @property
    def position(self) -> float:
        value = self._get("time_pos")
        return float(value) if value is not None else 0.0

    @property
    def duration(self) -> float:
        return float(self._get("duration", 0) or 0)

    @property
    def paused(self) -> bool:
        return bool(self._get("pause", True))

    @property
    def volume(self) -> float:
        return float(self._get("volume", 100) or 0)

    @property
    def muted(self) -> bool:
        return bool(self._get("mute", False))

    @property
    def speed(self) -> float:
        return float(self._get("speed", 1.0) or 1.0)

    @property
    def idle(self) -> bool:
        return self._get("path") is None

    @property
    def current_path(self) -> str:
        return self._get("path") or ""

    # ---------------------------------------------------------------- control

    def open(self, path: str, start_at: float = 0.0, paused: bool = False) -> None:
        self._ended = False
        options = {}
        if start_at > 0.5:
            options["start"] = f"{start_at:.3f}"
        try:
            self.mpv.loadfile(path, "replace", **options)
        except Exception as exc:  # noqa: BLE001
            self.engineError.emit(f"Could not open {path}: {exc}")
            return
        self._set("pause", paused)

    def stop(self) -> None:
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass

    def set_paused(self, paused: bool) -> None:
        self._set("pause", bool(paused))

    def toggle_pause(self) -> None:
        self._set("pause", not self.paused)

    def seek_relative(self, seconds: float) -> None:
        try:
            self.mpv.command("seek", f"{seconds:.3f}", "relative", "exact")
        except Exception:  # noqa: BLE001
            pass

    def seek_absolute(self, seconds: float) -> None:
        try:
            self.mpv.command("seek", f"{max(0.0, seconds):.3f}", "absolute", "exact")
        except Exception:  # noqa: BLE001
            pass

    def set_speed(self, speed: float) -> None:
        self._set("speed", max(0.25, min(5.0, float(speed))))

    def set_volume(self, volume: float) -> None:
        self._set("volume", max(0.0, min(200.0, float(volume))))

    def adjust_volume(self, delta: float) -> float:
        value = max(0.0, min(200.0, self.volume + delta))
        self.set_volume(value)
        if value > 0 and self.muted:
            self._set("mute", False)
        return value

    def set_muted(self, muted: bool) -> None:
        self._set("mute", bool(muted))

    def toggle_mute(self) -> None:
        self._set("mute", not self.muted)

    def set_normalisation(self, enabled: bool) -> None:
        """Even out narration that swings between whispering and shouting."""
        try:
            self.mpv.command("af", "set", NORMALISE_FILTER if enabled else "")
        except Exception as exc:  # noqa: BLE001
            self.engineError.emit(f"Volume levelling: {exc}")

    def chapters(self) -> list[dict]:
        """Chapter marks embedded in the file that is currently open."""
        raw = self._get("chapter_list", []) or []
        chapters = []
        for index, chapter in enumerate(raw, 1):
            try:
                start = float(chapter.get("time") or 0)
            except (TypeError, ValueError):
                continue
            chapters.append({"title": chapter.get("title") or f"Chapter {index}", "start": start})
        return chapters

    def shutdown(self) -> None:
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass
