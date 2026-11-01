"""The video surface: a QOpenGLWidget that libmpv renders into directly.

Using mpv's render API (rather than embedding an X11 window id) keeps playback
native on Wayland and lets ordinary Qt widgets be drawn on top of the video.
"""

import ctypes
import locale

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import mpv

def use_c_numeric_locale() -> None:
    """libmpv refuses to run unless LC_NUMERIC is "C".

    Qt resets the process locale when QApplication is constructed, so this has
    to be called again after the application object exists.
    """
    locale.setlocale(locale.LC_NUMERIC, "C")


use_c_numeric_locale()

HOLD_DELAY_MS = 190  # press-and-hold longer than this means "speed up", not "click"

# GL enums we need to put the context back the way Qt expects it. Importing a
# full GL binding for four constants would not earn its keep.
GL_BLEND = 0x0BE2
GL_FRAMEBUFFER = 0x8D40
GL_TEXTURE0 = 0x84C0
GL_TEXTURE_2D = 0x0DE1



def _address_of(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return ctypes.cast(value, ctypes.c_void_p).value or 0
    except (ctypes.ArgumentError, TypeError):
        return 0


def _get_proc_address(_ctx, name):
    gl_context = QOpenGLContext.currentContext()
    if gl_context is None:
        return 0
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    return _address_of(gl_context.getProcAddress(name))


# Must outlive the render context, so keep a module-level reference.
_PROC_ADDRESS_FN = mpv.MpvGlGetProcAddressFn(_get_proc_address)


class MpvWidget(QOpenGLWidget):
    """Owns the mpv instance and turns its state into Qt signals."""

    positionChanged = Signal(float)
    durationChanged = Signal(float)
    pausedChanged = Signal(bool)
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)
    speedChanged = Signal(float)
    tracksChanged = Signal(list)
    fileLoaded = Signal()
    endReached = Signal(str)
    bufferingChanged = Signal(bool)
    engineError = Signal(str)
    frameRendered = Signal()

    clicked = Signal()
    doubleClicked = Signal()
    holdSpeedChanged = Signal(bool)
    pointerActivity = Signal()
    wheelVolume = Signal(int)

    _renderRequested = Signal()

    def __init__(self, parent=None, hwdec: str = "auto-safe"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(160, 90)

        self._ctx = None
        self._destroyed = False
        self._duration = 0.0
        self._position = 0.0
        self._hold_active = False
        self._hold_rate = 2.0
        self._normal_speed = 1.0

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(HOLD_DELAY_MS)
        self._hold_timer.timeout.connect(self._begin_hold)

        use_c_numeric_locale()
        self.mpv = mpv.MPV(
            vo="libmpv",
            hwdec=hwdec,
            idle="yes",
            keep_open="yes",          # we decide what happens at end of file
            osc=False,
            osd_level=0,
            input_default_bindings=False,
            input_vo_keyboard=False,
            ytdl=False,
            volume_max=200,
            audio_display="no",
            cache="yes",
            log_handler=self._on_log,
            loglevel="error",
        )
        self._connect_properties()
        self._renderRequested.connect(self.update, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- mpv wiring

    def _on_log(self, level, prefix, text):
        message = f"{prefix}: {text.strip()}"
        if level in ("fatal", "error"):
            self.engineError.emit(message)

    def _connect_properties(self) -> None:
        player = self.mpv

        @player.property_observer("time-pos")
        def _time(_name, value):
            if value is not None:
                self._position = float(value)
                self.positionChanged.emit(float(value))

        @player.property_observer("duration")
        def _dur(_name, value):
            self._duration = float(value or 0)
            self.durationChanged.emit(self._duration)

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

        @player.property_observer("track-list")
        def _tracks(_name, value):
            self.tracksChanged.emit(list(value or []))

        @player.property_observer("eof-reached")
        def _eof(_name, value):
            # keep-open holds the last frame instead of ending the file, so the
            # end-file event never arrives; this property is what actually fires.
            if value:
                self.endReached.emit("eof")

        @player.property_observer("paused-for-cache")
        def _cache(_name, value):
            self.bufferingChanged.emit(bool(value))

        @player.event_callback("file-loaded")
        def _loaded(_event):
            self.fileLoaded.emit()

        @player.event_callback("end-file")
        def _end(event):
            reason = getattr(getattr(event, "data", None), "reason", None)
            names = {0: "eof", 1: "restarted", 2: "aborted", 3: "quit", 4: "error", 5: "redirect"}
            self.endReached.emit(names.get(reason, "unknown"))

        # Keep strong references so the closures are not garbage collected.
        self._observers = (_time, _dur, _pause, _volume, _mute, _speed, _tracks, _eof,
                           _cache, _loaded, _end)

    # ------------------------------------------------------------- GL surface

    def initializeGL(self) -> None:
        if self._ctx is not None:
            return
        try:
            self._ctx = mpv.MpvRenderContext(
                self.mpv, "opengl",
                opengl_init_params={"get_proc_address": _PROC_ADDRESS_FN},
            )
            self._ctx.update_cb = self._on_render_update
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a message
            self._ctx = None
            self.engineError.emit(f"Could not start the video renderer: {exc}")

    def _on_render_update(self) -> None:
        # Called from mpv's render thread; hop to the GUI thread to repaint.
        if not self._destroyed:
            self._renderRequested.emit()

    def paintGL(self) -> None:
        ctx = self._ctx
        if ctx is None or self._destroyed:
            return
        width, height = self.framebuffer_size()
        target = self.defaultFramebufferObject()
        ctx.render(
            flip_y=True,
            opengl_fbo={"w": width, "h": height, "fbo": target},
        )
        self._restore_gl_state(target)
        self.frameRendered.emit()

    def framebuffer_size(self) -> tuple[int, int]:
        """Size of the framebuffer Qt allocated for this widget, in device pixels.

        Qt uses size() * devicePixelRatio, ROUNDED. Truncating instead would, at
        fractional scaling such as 105 %, describe a buffer one pixel smaller
        than the real one for many window sizes; mpv then writes each row at the
        wrong offset and the frame tears into horizontal dashes of garbage.
        """
        ratio = self.devicePixelRatioF()
        return (max(1, round(self.width() * ratio)),
                max(1, round(self.height() * ratio)))

    def _restore_gl_state(self, target: int) -> None:
        """Undo the GL state mpv leaves behind.

        mpv's renderer does not promise to restore the context, and it returns
        with framebuffer 0 bound and blending still enabled. Qt then composites
        this widget assuming its own framebuffer is current and that blending is
        off, which tears rows of the frame into garbage — most visibly when
        subtitles are on, because drawing them is what turns blending on.
        """
        gl_context = QOpenGLContext.currentContext()
        if gl_context is None:
            return
        functions = gl_context.functions()
        functions.glBindFramebuffer(GL_FRAMEBUFFER, target)
        functions.glDisable(GL_BLEND)
        functions.glActiveTexture(GL_TEXTURE0)
        functions.glBindTexture(GL_TEXTURE_2D, 0)

    def shutdown(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.mpv.command("stop")
        except Exception:  # noqa: BLE001
            pass
        if self._ctx is not None:
            try:
                self.makeCurrent()
                self._ctx.free()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._ctx = None
                self.doneCurrent()
        try:
            self.mpv.terminate()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ playback API

    def _set(self, name: str, value) -> None:
        try:
            setattr(self.mpv, name.replace("-", "_"), value)
        except Exception as exc:  # noqa: BLE001
            self.engineError.emit(f"{name}: {exc}")

    def _get(self, name: str, default=None):
        try:
            return getattr(self.mpv, name.replace("-", "_"))
        except Exception:  # noqa: BLE001
            return default

    def mpv_get(self, name: str, default=None):
        """Read an mpv property (dashes or underscores both work)."""
        return self._get(name, default)

    def mpv_set(self, name: str, value) -> None:
        """Write an mpv property."""
        self._set(name, value)

    def open(self, path: str, start_at: float = 0.0, paused: bool = False) -> None:
        """Load a file, optionally starting partway in.

        The start position is passed as a file-local option so mpv seeks while
        it opens, instead of blocking the UI thread waiting to seek afterwards.
        """
        self._duration = 0.0
        self._position = start_at
        options = {}
        if start_at > 1:
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

    @property
    def position(self) -> float:
        value = self._get("time_pos")
        return float(value) if value is not None else self._position

    @property
    def duration(self) -> float:
        value = self._get("duration")
        return float(value) if value else self._duration

    @property
    def paused(self) -> bool:
        return bool(self._get("pause", True))

    @property
    def muted(self) -> bool:
        return bool(self._get("mute", False))

    @property
    def volume(self) -> float:
        return float(self._get("volume", 100) or 0)

    @property
    def speed(self) -> float:
        return float(self._get("speed", 1.0) or 1.0)

    @property
    def idle(self) -> bool:
        return self._get("path") is None

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
        target = max(0.0, seconds)
        if self._duration:
            target = min(target, max(0.0, self._duration - 0.1))
        try:
            self.mpv.command("seek", f"{target:.3f}", "absolute", "exact")
        except Exception:  # noqa: BLE001
            pass

    def seek_percent(self, percent: float) -> None:
        try:
            self.mpv.command("seek", f"{percent:.2f}", "absolute-percent", "exact")
        except Exception:  # noqa: BLE001
            pass

    def frame_step(self, forward: bool = True) -> None:
        try:
            self.mpv.command("frame-step" if forward else "frame-back-step")
        except Exception:  # noqa: BLE001
            pass

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

    def set_speed(self, speed: float) -> None:
        speed = max(0.1, min(6.0, float(speed)))
        if not self._hold_active:
            self._normal_speed = speed
        self._set("speed", speed)

    def set_hold_rate(self, rate: float) -> None:
        self._hold_rate = max(1.25, min(6.0, float(rate)))

    def screenshot(self, path: str) -> None:
        try:
            self.mpv.command("screenshot-to-file", path, "video")
        except Exception as exc:  # noqa: BLE001
            self.engineError.emit(f"Screenshot failed: {exc}")

    def tracks(self) -> list:
        return list(self._get("track_list", []) or [])

    def chapters(self) -> list[dict]:
        """Chapter marks embedded in the file, as [{title, start}] in seconds."""
        marks = []
        for index, chapter in enumerate(self._get("chapter_list", []) or [], 1):
            try:
                start = float(chapter.get("time") or 0)
            except (TypeError, ValueError):
                continue
            marks.append({"title": chapter.get("title") or f"Chapter {index}",
                          "start": start})
        return marks

    def cycle_track(self, kind: str) -> str:
        """Cycle subtitle or audio tracks; returns a label for the on-screen toast."""
        prop = "sid" if kind == "sub" else "aid"
        try:
            self.mpv.command("cycle", prop)
        except Exception:  # noqa: BLE001
            return ""
        current = self._get(prop)
        if current in (False, None, "no"):
            return "Subtitles off" if kind == "sub" else "Audio off"
        for track in self.tracks():
            if track.get("type") == ("sub" if kind == "sub" else "audio") and track.get("id") == current:
                title = track.get("title") or track.get("lang") or f"track {current}"
                kind_label = "Subtitles" if kind == "sub" else "Audio"
                return f"{kind_label}: {title}"
        return f"{'Subtitles' if kind == 'sub' else 'Audio'} {current}"

    def set_subtitle_delay(self, delta: float) -> float:
        value = float(self._get("sub_delay", 0.0) or 0.0) + delta
        self._set("sub_delay", value)
        return value

    # ----------------------------------------------------------------- input

    def _begin_hold(self) -> None:
        if self.paused:
            return
        self._normal_speed = self.speed
        self._hold_active = True
        self._set("speed", self._hold_rate)
        self.holdSpeedChanged.emit(True)

    def _end_hold(self) -> bool:
        if not self._hold_active:
            return False
        self._hold_active = False
        self._set("speed", self._normal_speed)
        self.holdSpeedChanged.emit(False)
        return True

    @property
    def hold_rate(self) -> float:
        return self._hold_rate

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.idle:
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_holding = self._end_hold()
            if self._hold_timer.isActive():
                self._hold_timer.stop()
                if not was_holding:
                    self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._hold_timer.stop()
            self._end_hold()
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self.pointerActivity.emit()
        super().mouseMoveEvent(event)

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y()
        if steps:
            self.wheelVolume.emit(5 if steps > 0 else -5)
            event.accept()
            return
        super().wheelEvent(event)

    def leaveEvent(self, event) -> None:
        self._hold_timer.stop()
        self._end_hold()
        super().leaveEvent(event)
