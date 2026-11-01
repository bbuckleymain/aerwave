"""The playback page: video surface, overlay controls and every QOL shortcut."""

import os
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton,
    QVBoxLayout, QWidget,
)

from . import style
from .icons import icon
from .idle import IdleInhibitor
from .mpvwidget import MpvWidget
from . import subtitles
from .paths import SCREENSHOT_DIR
from .widgets import ElidedLabel, SeekBar, Toast, VolumeBar, format_time

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
SUBTITLE_LANGUAGES = [
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("ja", "Japanese"), ("ko", "Korean"),
    ("zh", "Chinese"), ("ru", "Russian"), ("", "No preference"),
]
CONTROL_HIDE_MS = 2600
SAVE_EVERY_MS = 4000
RESUME_MIN = 12.0          # don't offer to resume the first few seconds
FINISH_TAIL = 20.0         # a long video is finished once this close to the end
FINISH_RATIO = 0.97        # ...and it must be at least this far through


def is_finished(position: float, duration: float) -> bool:
    """Whether a stop at `position` should count as having watched the whole thing.

    The tail scales with length, so 30 s into a 45 s clip is still "in progress"
    while 20 s from the end of a feature film counts as finished.
    """
    if duration <= 0:
        return False
    tail = min(FINISH_TAIL, duration * 0.06)
    return position >= duration - tail and position / duration >= FINISH_RATIO


def _track_id(value) -> str:
    """mpv reports a deselected track as False; it only accepts 'no' back."""
    if value in (False, None, "", "False"):
        return "no"
    return str(value)


def _tool_button(name: str, tip: str, size: int = 22, colour: str = style.TEXT) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size, colour))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedSize(size + 14, size + 14)
    return button


class PlayerPage(QWidget):
    """Owns the mpv surface and everything drawn on top of it."""

    backRequested = Signal()
    stepRequested = Signal(int)        # +1 next, -1 previous
    progressChanged = Signal(int)      # video_id whose stored progress moved
    seeked = Signal(float)             # position jumped, in seconds
    fullscreenRequested = Signal(bool)
    helpRequested = Signal()
    transcribeRequested = Signal(dict)   # ask for generated subtitles
    cancelTranscribeRequested = Signal(int)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerRoot")
        self.library = library
        self.video: dict | None = None
        self._duration = 0.0
        self._position = 0.0
        self._counted_play = False
        self._marked_watched = False
        self._ended = False
        self.chapters: list[dict] = []
        self._transcribing = False
        self._pending_tracks: tuple | None = None   # (audio, subtitle) to restore
        self._controls_locked = False   # pointer is over the controls
        self._is_fullscreen = False

        # Held while a video is actually rolling, so the screen does not dim.
        self.inhibitor = IdleInhibitor(self)

        # Software decoding by default, which is also mpv's own default. Feeding
        # hardware-decoded surfaces through the render API misrenders on some
        # driver and GPU combinations — rows of the frame tear into coloured
        # dashes, most visibly once subtitles add an overlay pass. Modern CPUs
        # handle 1080p comfortably; turn it back on in the gear menu if you want
        # it for 4K.
        hwdec = os.environ.get("MPV_HWDEC") or (
            "auto-safe" if library.get("hardware_decoding", False) else "no"
        )
        self.video_widget = MpvWidget(self, hwdec=hwdec)
        self.video_widget.setObjectName("VideoSurface")
        self._apply_subtitle_preferences()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_widget)

        self._build_top_bar()
        self._build_controls()

        self.toast = Toast(self, anchor="top")
        self.speed_badge = Toast(self, object_name="SpeedBadge", anchor="bottom")

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(CONTROL_HIDE_MS)
        self._hide_timer.timeout.connect(self._hide_chrome)

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(SAVE_EVERY_MS)
        self._save_timer.timeout.connect(self._on_save_tick)

        self._wire_engine()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.video_widget.installEventFilter(self)

        self.video_widget.set_volume(float(library.get("volume", 100)))
        self.video_widget.set_muted(bool(library.get("muted", False)))
        self.video_widget.set_hold_rate(float(library.get("hold_speed", 2.0)))
        self._sync_controls()

    def set_transcribing(self, active: bool) -> None:
        self._transcribing = active
        if not active:
            self.clear_transcribe_progress()

    def show_transcribe_progress(self, fraction: float, phase: str) -> None:
        self.subtitle_status.setText(
            f"Subtitles: {phase.lower()} {int(fraction * 100)}%" if fraction > 0
            else f"Subtitles: {phase.lower()}")
        self.subtitle_status.show()

    def clear_transcribe_progress(self) -> None:
        self.subtitle_status.hide()
        self.subtitle_status.setText("")

    def has_subtitle_track(self) -> bool:
        return any(track.get("type") == "sub" for track in self.video_widget.tracks())

    def _apply_subtitle_preferences(self) -> None:
        """Pick up subtitle files sitting beside the video, and prefer a language."""
        # "fuzzy" also matches "Movie.en.srt", not just an exact name match.
        self.video_widget.mpv_set("sub-auto", "fuzzy")
        self.video_widget.mpv_set("audio-file-auto", "fuzzy")
        # Rips often park subtitles in a folder of their own.
        self.video_widget.mpv_set("sub-file-paths",
                                  "Subs:subs:Subtitles:subtitles:Sub:sub")
        language = str(self.library.get("subtitle_language", "en") or "").strip()
        if language:
            self.video_widget.mpv_set("slang", language)

    def _sync_controls(self) -> None:
        """Push current engine state into the widgets (observers only fire on change)."""
        self._on_paused(self.video_widget.paused)
        self._on_volume(self.video_widget.volume)
        self._on_muted(self.video_widget.muted)
        self._on_speed(self.video_widget.speed)

    # ------------------------------------------------------------------- setup

    def _build_top_bar(self) -> None:
        self.top_bar = QWidget(self)
        self.top_bar.setObjectName("PlayerTopBar")
        row = QHBoxLayout(self.top_bar)
        row.setContentsMargins(10, 8, 14, 8)
        row.setSpacing(10)

        self.back_button = _tool_button("back", "Back to library  (Esc)")
        self.back_button.clicked.connect(self.backRequested)
        row.addWidget(self.back_button)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title_label = ElidedLabel("")
        self.title_label.setObjectName("PlayerTitle")
        self.subtitle_label = ElidedLabel("")
        self.subtitle_label.setObjectName("PlayerSubtitle")
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        row.addLayout(titles, 1)

        self.subtitle_status = QLabel("")
        self.subtitle_status.setObjectName("PlayerSubtitle")
        self.subtitle_status.hide()
        row.addWidget(self.subtitle_status)

        help_button = _tool_button("gear", "Preferences and shortcuts")
        help_button.clicked.connect(self._open_settings_menu)
        self.settings_button_top = help_button
        row.addWidget(help_button)

    def _build_controls(self) -> None:
        self.control_bar = QWidget(self)
        self.control_bar.setObjectName("ControlBar")
        outer = QVBoxLayout(self.control_bar)
        outer.setContentsMargins(14, 8, 14, 10)
        outer.setSpacing(4)

        self.seek_bar = SeekBar()
        self.seek_bar.seeked.connect(self._on_seek_committed)
        self.seek_bar.scrubbing.connect(self._on_scrubbing)
        self.seek_bar.scrubStateChanged.connect(self._on_scrub_state)
        self.seek_bar.hovered.connect(self._on_seek_hover)
        self.seek_bar.unhovered.connect(lambda: self.hover_time.hide())
        outer.addWidget(self.seek_bar)

        # Parented to the page, not the bar, so it can sit above the bar's edge.
        self.hover_time = QLabel(self)
        self.hover_time.setObjectName("Toast")
        self.hover_time.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hover_time.hide()

        row = QHBoxLayout()
        row.setSpacing(2)
        outer.addLayout(row)

        self.play_button = _tool_button("play", "Play / pause  (Space)", 24)
        self.play_button.clicked.connect(self.toggle_pause)
        self.prev_button = _tool_button("prev", "Previous video  (P)", 20)
        self.prev_button.clicked.connect(lambda: self.stepRequested.emit(-1))
        self.next_button = _tool_button("next", "Next video  (N)", 20)
        self.next_button.clicked.connect(lambda: self.stepRequested.emit(1))
        self.back5_button = _tool_button("back5", "Back 5 seconds  (←)", 21)
        self.back5_button.clicked.connect(lambda: self.skip(-5))
        self.forward5_button = _tool_button("forward5", "Forward 5 seconds  (→)", 21)
        self.forward5_button.clicked.connect(lambda: self.skip(5))

        for widget in (self.prev_button, self.back5_button, self.play_button,
                       self.forward5_button, self.next_button):
            row.addWidget(widget)

        row.addSpacing(6)
        self.mute_button = _tool_button("volume", "Mute  (M)", 20)
        self.mute_button.clicked.connect(self.toggle_mute)
        row.addWidget(self.mute_button)
        self.volume_bar = VolumeBar()
        self.volume_bar.changed.connect(self._on_volume_bar)
        row.addWidget(self.volume_bar)
        self.volume_label = QLabel("100%")
        self.volume_label.setObjectName("TimeLabel")
        self.volume_label.setFixedWidth(42)
        row.addWidget(self.volume_label)

        row.addSpacing(10)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("TimeLabel")
        row.addWidget(self.time_label)

        row.addStretch(1)

        self.speed_button = QPushButton("1×")
        self.speed_button.setObjectName("Flat")
        self.speed_button.setToolTip("Playback speed  ([ and ])")
        self.speed_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed_button.setFixedHeight(34)
        self.speed_button.setMinimumWidth(48)
        self.speed_button.clicked.connect(self._open_speed_menu)
        row.addWidget(self.speed_button)

        self.tracks_button = _tool_button("subtitles", "Audio and subtitle tracks  (V / A)", 21)
        self.tracks_button.clicked.connect(self._open_tracks_menu)
        row.addWidget(self.tracks_button)

        self.settings_button = _tool_button("gear", "Preferences", 20)
        self.settings_button.clicked.connect(self._open_settings_menu)
        row.addWidget(self.settings_button)

        self.fullscreen_button = _tool_button("fullscreen", "Fullscreen  (F)", 21)
        self.fullscreen_button.clicked.connect(lambda: self.set_fullscreen(not self._is_fullscreen))
        row.addWidget(self.fullscreen_button)

        for bar in (self.control_bar, self.top_bar):
            effect = QGraphicsOpacityEffect(bar)
            effect.setOpacity(1.0)
            bar.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", bar)
            animation.setDuration(170)
            bar.setProperty("effect", effect)
            bar.setProperty("animation", animation)
        self.control_bar.installEventFilter(self)
        self.top_bar.installEventFilter(self)

    def _wire_engine(self) -> None:
        widget = self.video_widget
        widget.positionChanged.connect(self._on_position)
        widget.durationChanged.connect(self._on_duration)
        widget.pausedChanged.connect(self._on_paused)
        widget.volumeChanged.connect(self._on_volume)
        widget.mutedChanged.connect(self._on_muted)
        widget.speedChanged.connect(self._on_speed)
        widget.endReached.connect(self._on_end)
        widget.engineError.connect(self._on_engine_error)
        widget.clicked.connect(self.toggle_pause)
        widget.doubleClicked.connect(lambda: self.set_fullscreen(not self._is_fullscreen))
        widget.holdSpeedChanged.connect(self._on_hold_speed)
        widget.pointerActivity.connect(self.wake_chrome)
        widget.wheelVolume.connect(lambda delta: self.adjust_volume(delta))
        widget.fileLoaded.connect(self._on_file_loaded)
        widget.frameRendered.connect(self._repaint_chrome)
        widget.bufferingChanged.connect(
            lambda busy: self.toast.flash("Buffering…", 800) if busy else None
        )

    def _repaint_chrome(self) -> None:
        """Redraw the overlays after every video frame.

        Widgets stacked on a QOpenGLWidget are composited from their own cached
        surfaces; without this, a slice of the bar can keep showing an old video
        frame behind its translucent background.
        """
        for widget in (self.control_bar, self.top_bar, self.toast, self.speed_badge,
                       self.hover_time):
            if widget.isVisible():
                widget.update()

    # ------------------------------------------------------------------ layout

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.top_bar.setGeometry(0, 0, self.width(), self.top_bar.sizeHint().height())
        bar_height = self.control_bar.sizeHint().height()
        self.control_bar.setGeometry(0, self.height() - bar_height, self.width(), bar_height)
        self.top_bar.raise_()
        self.control_bar.raise_()
        self.toast.raise_()
        self.speed_badge.raise_()

    # ---------------------------------------------------------------- playback

    def play(self, video: dict, resume: bool = True, start_at: float | None = None) -> None:
        self.save_progress(flush=True)
        self.video = video
        self._counted_play = False
        self._marked_watched = False
        self._ended = False
        self._duration = float(video.get("duration") or 0)
        self.title_label.setText(video["title"])
        parent_dir = Path(video["path"]).parent.name
        self.subtitle_label.setText(parent_dir)
        self.seek_bar.set_duration(self._duration)
        self.seek_bar.set_position(0)
        self.setWindowTitle(video["title"])

        position = float(video.get("position") or 0)
        if start_at is not None:
            begin = max(0.0, start_at)
        elif resume and self.library.get("resume_playback", True) and not video.get("watched"):
            resumable = position > RESUME_MIN and not is_finished(position, self._duration)
            begin = position if resumable else 0.0
        else:
            begin = 0.0

        self._pending_tracks = (video.get("audio_track"), video.get("sub_track"))
        self.chapters = []
        self.seek_bar.set_marks([])
        self.video_widget.open(video["path"], start_at=begin)
        self.video_widget.set_speed(1.0)
        self._save_timer.start()
        self._sync_controls()
        QTimer.singleShot(250, self._sync_controls)
        self._refresh_idle_inhibit()
        self.wake_chrome()
        if begin > 1:
            self.toast.flash(f"Resumed at {format_time(begin)}  ·  Backspace to start over", 2600)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def stop(self) -> None:
        self.save_progress(flush=True)
        self._save_timer.stop()
        self.video_widget.stop()
        self.video = None
        self.inhibitor.release()

    def shutdown_timers(self) -> None:
        """Called before the window closes, so nothing fires against a closed database."""
        self._save_timer.stop()
        self._hide_timer.stop()
        self.inhibitor.release()

    def _on_save_tick(self) -> None:
        self.save_progress()
        # Re-assert the hold periodically: if a pause notification were ever
        # missed, the screen would otherwise stay awake (or dim) indefinitely.
        self._refresh_idle_inhibit()

    def save_progress(self, flush: bool = False, watched: bool | None = None) -> None:
        if not self.video:
            return
        position = self._position
        duration = self._duration or self.video_widget.duration
        if is_finished(position, duration):
            watched = True if watched is None else watched
            position = 0.0
        if position <= 1 and watched is None and not flush:
            return
        self.library.save_progress(
            self.video["id"], position, watched=watched,
            bump_count=self._take_play_count(),
            audio_track=_track_id(self.video_widget.mpv_get("aid")),
            sub_track=_track_id(self.video_widget.mpv_get("sid")),
        )
        self.progressChanged.emit(self.video["id"])

    def _take_play_count(self) -> bool:
        if self._counted_play or self._position < 20:
            return False
        self._counted_play = True
        return True

    # ------------------------------------------------------------- transport

    def toggle_pause(self) -> None:
        self.video_widget.toggle_pause()
        self.wake_chrome()

    def skip(self, seconds: float) -> None:
        self.video_widget.seek_relative(seconds)
        self.seeked.emit(max(0.0, self._position + seconds))
        self.toast.flash(f"{'+' if seconds > 0 else '−'}{abs(int(seconds))}s", 700)
        self.wake_chrome()

    def adjust_volume(self, delta: float) -> None:
        value = self.video_widget.adjust_volume(delta)
        self.library.set("volume", round(value, 1))
        self.toast.flash(f"Volume {int(round(value))}%", 900)
        self.wake_chrome()

    def toggle_mute(self) -> None:
        self.video_widget.toggle_mute()
        self.library.set("muted", self.video_widget.muted)
        self.toast.flash("Muted" if self.video_widget.muted else "Unmuted", 900)
        self.wake_chrome()

    def nudge_speed(self, direction: int) -> None:
        """Step to the next preset speed up or down the ladder."""
        current = self.video_widget.speed
        if direction > 0:
            target = next((s for s in SPEEDS if s > current + 1e-3), SPEEDS[-1])
        else:
            target = next((s for s in reversed(SPEEDS) if s < current - 1e-3), SPEEDS[0])
        self.set_speed(target)

    def set_speed(self, speed: float) -> None:
        self.video_widget.set_speed(speed)
        self.toast.flash(f"Speed {speed:g}×", 900)
        self.wake_chrome()

    def set_fullscreen(self, enabled: bool) -> None:
        self._is_fullscreen = bool(enabled)
        self.fullscreen_button.setIcon(
            icon("fullscreen_exit" if enabled else "fullscreen", 21, style.TEXT)
        )
        self.fullscreenRequested.emit(self._is_fullscreen)
        self.wake_chrome()

    def restart(self) -> None:
        self.video_widget.seek_absolute(0)
        self.video_widget.set_paused(False)
        self.toast.flash("Restarted", 900)

    def toggle_watched(self) -> None:
        if not self.video:
            return
        new_state = not bool(self.video.get("watched"))
        self.library.set_watched(self.video["id"], new_state, reset_position=new_state)
        self.video["watched"] = 1 if new_state else 0
        self._marked_watched = new_state
        self.progressChanged.emit(self.video["id"])
        self.toast.flash("Marked as watched" if new_state else "Marked as unwatched")

    def take_screenshot(self) -> None:
        if not self.video:
            return
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c for c in self.video["title"] if c.isalnum() or c in " -_")[:60].strip()
        target = SCREENSHOT_DIR / f"{safe or 'aerwave'} {stamp}.png"
        self.video_widget.screenshot(str(target))
        self.toast.flash(f"Saved {target.name}", 1800)

    # ------------------------------------------------------- engine callbacks

    def _on_file_loaded(self) -> None:
        """The file is open: pick up its chapters and restore track choices."""
        self.chapters = self.video_widget.chapters()
        self.seek_bar.set_marks([c["start"] for c in self.chapters[1:]])
        QTimer.singleShot(400, self.maybe_autogenerate)
        if self._pending_tracks is not None:
            audio, subtitle = self._pending_tracks
            self._pending_tracks = None
            for prop, value in (("aid", audio), ("sid", subtitle)):
                value = _track_id(value) if value is not None else None
                # "no" for audio would leave a silent video; only honour it for subs.
                if value and not (prop == "aid" and value == "no"):
                    self.video_widget.mpv_set(prop, value)

    def chapter_at(self, position: float) -> int:
        for index in range(len(self.chapters) - 1, -1, -1):
            if position >= self.chapters[index]["start"] - 0.001:
                return index
        return 0

    def next_chapter(self) -> None:
        if not self.chapters:
            self.skip(60)
            return
        index = self.chapter_at(self._position) + 1
        if index >= len(self.chapters):
            self.toast.flash("Last chapter")
            return
        self.video_widget.seek_absolute(self.chapters[index]["start"])
        self.toast.flash(self.chapters[index]["title"], 1500)

    def previous_chapter(self) -> None:
        if not self.chapters:
            self.skip(-60)
            return
        index = self.chapter_at(self._position)
        start = self.chapters[index]["start"]
        # Restart this chapter unless you only just entered it.
        if self._position - start > 3 or index == 0:
            self.video_widget.seek_absolute(start)
        else:
            index -= 1
            self.video_widget.seek_absolute(self.chapters[index]["start"])
        self.toast.flash(self.chapters[index]["title"], 1500)

    def _on_position(self, position: float) -> None:
        self._position = position
        if self._ended and self._duration and position < self._duration - 5:
            self._ended = False   # they seeked back; let the ending count again
        self.seek_bar.set_position(position)
        self._update_time_label()
        if not self._marked_watched and is_finished(position, self._duration):
            self._marked_watched = True
            if self.video:
                self.library.set_watched(self.video["id"], True, reset_position=True)
                self.video["watched"] = 1
                self.progressChanged.emit(self.video["id"])

    def _on_duration(self, duration: float) -> None:
        if duration > 0:
            self._duration = duration
            self.seek_bar.set_duration(duration)
            self._update_time_label()
            if self.video and abs((self.video.get("duration") or 0) - duration) > 1:
                self.video["duration"] = duration

    def _update_time_label(self) -> None:
        long_form = self._duration >= 3600
        self.time_label.setText(
            f"{format_time(self._position, long_form)} / {format_time(self._duration, long_form)}"
        )

    def _on_paused(self, paused: bool) -> None:
        self.play_button.setIcon(icon("play" if paused else "pause", 24, style.TEXT))
        self._refresh_idle_inhibit(paused)
        if paused:
            self.wake_chrome(sticky=True)
            self.save_progress()
        else:
            self.wake_chrome()

    def _refresh_idle_inhibit(self, paused: bool | None = None) -> None:
        """Keep the screen awake exactly while a video is playing.

        `paused` comes straight from the property change that triggered this;
        re-reading it from mpv here would race the change we are reacting to.
        """
        if paused is None:
            paused = self.video_widget.paused
        playing = self.video is not None and not paused
        reason = f"Watching {self.video['title']}" if playing and self.video else "Playing video"
        self.inhibitor.set_active(playing, reason)

    def _on_volume(self, volume: float) -> None:
        self.volume_bar.set_value(volume)
        self.volume_label.setText(f"{int(round(volume))}%")
        self._refresh_volume_icon()

    def _on_muted(self, muted: bool) -> None:
        self.volume_bar.set_muted(muted)
        self._refresh_volume_icon()

    def _refresh_volume_icon(self) -> None:
        volume = self.video_widget.volume
        if self.video_widget.muted or volume <= 0:
            name = "volume_mute"
        elif volume > 100:
            name = "volume_boost"
        elif volume < 45:
            name = "volume_low"
        else:
            name = "volume"
        self.mute_button.setIcon(icon(name, 20, style.TEXT))

    def _on_speed(self, speed: float) -> None:
        self.speed_button.setText(f"{speed:g}×")

    def _on_hold_speed(self, active: bool) -> None:
        if active:
            self.speed_badge.show_sticky(f"{self.video_widget.hold_rate:g}×  ▶▶")
        else:
            self.speed_badge.dismiss()

    def _on_end(self, reason: str) -> None:
        # Both the end-file event and the eof-reached property can report the
        # same ending; only act on the first one.
        if reason != "eof" or not self.video or self._ended:
            return
        self._ended = True
        self.library.set_watched(self.video["id"], True, reset_position=True)
        self.video["watched"] = 1
        self._marked_watched = True
        self.progressChanged.emit(self.video["id"])
        if self.library.get("autoplay_next", True):
            self.stepRequested.emit(1)
        else:
            self.wake_chrome(sticky=True)
            self.toast.flash("Finished", 1600)

    def _on_engine_error(self, message: str) -> None:
        if "INVALID_ENUM" in message or "libmpv_render" in message:
            return  # harmless capability probing during renderer start-up
        self.toast.flash(message, 3200)

    # ------------------------------------------------------------ seek bar UI

    def _on_seek_committed(self, position: float) -> None:
        self.video_widget.seek_absolute(position)
        self.seeked.emit(position)
        self.wake_chrome()

    def _on_scrubbing(self, position: float) -> None:
        self._position = position
        self._update_time_label()

    def _on_scrub_state(self, active: bool) -> None:
        self._controls_locked = active
        if active:
            self.wake_chrome(sticky=True)
        else:
            self.wake_chrome()

    def _on_seek_hover(self, seconds: float, x: int) -> None:
        label = format_time(seconds, self._duration >= 3600)
        if self.chapters:
            label = f"{label}  ·  {self.chapters[self.chapter_at(seconds)]['title']}"
        self.hover_time.setText(label)
        self.hover_time.adjustSize()
        anchor = self.seek_bar.mapTo(self, self.seek_bar.rect().topLeft())
        left = anchor.x() + x - self.hover_time.width() // 2
        left = max(8, min(self.width() - self.hover_time.width() - 8, left))
        self.hover_time.move(left, anchor.y() - self.hover_time.height() - 6)
        self.hover_time.show()
        self.hover_time.raise_()

    def _on_volume_bar(self, value: float) -> None:
        self.video_widget.set_volume(value)
        if value > 0 and self.video_widget.muted:
            self.video_widget.set_muted(False)
        self.library.set("volume", round(value, 1))

    # --------------------------------------------------------------- chrome

    def wake_chrome(self, sticky: bool = False) -> None:
        self._set_chrome_visible(True)
        self._hide_timer.stop()
        if not sticky and not self.video_widget.paused and not self._controls_locked:
            self._hide_timer.start()

    def _hide_chrome(self) -> None:
        if self._controls_locked or self.video_widget.paused or self.video is None:
            return
        if self.control_bar.underMouse() or self.top_bar.underMouse():
            return
        self._set_chrome_visible(False)

    def _set_chrome_visible(self, visible: bool) -> None:
        for bar in (self.control_bar, self.top_bar):
            effect = bar.property("effect")
            animation = bar.property("animation")
            if effect is None or animation is None:
                continue
            target = 1.0 if visible else 0.0
            if abs(effect.opacity() - target) < 0.01:
                continue
            animation.stop()
            animation.setStartValue(effect.opacity())
            animation.setEndValue(target)
            animation.start()
        if not visible:
            self.hover_time.hide()
        self.video_widget.setCursor(
            Qt.CursorShape.ArrowCursor if visible else Qt.CursorShape.BlankCursor
        )

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter and obj in (self.control_bar, self.top_bar):
            self._controls_locked = True
            self.wake_chrome(sticky=True)
        elif event.type() == QEvent.Type.Leave and obj in (self.control_bar, self.top_bar):
            self._controls_locked = self.seek_bar.dragging
            self.wake_chrome()
        elif event.type() == QEvent.Type.MouseMove:
            self.wake_chrome()
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------------- menus

    def _open_speed_menu(self) -> None:
        menu = QMenu(self)
        current = round(self.video_widget.speed, 3)
        for speed in SPEEDS:
            action = menu.addAction(f"{speed:g}×")
            action.setCheckable(True)
            action.setChecked(abs(speed - current) < 1e-3)
            action.triggered.connect(lambda _checked=False, s=speed: self.set_speed(s))
        menu.addSeparator()
        hold = menu.addMenu("Hold-click speed")
        for rate in (1.5, 2.0, 2.5, 3.0, 4.0):
            action = hold.addAction(f"{rate:g}×")
            action.setCheckable(True)
            action.setChecked(abs(self.video_widget.hold_rate - rate) < 1e-3)
            action.triggered.connect(lambda _checked=False, r=rate: self._set_hold_rate(r))
        self._popup(menu, self.speed_button)

    def _set_hold_rate(self, rate: float) -> None:
        self.video_widget.set_hold_rate(rate)
        self.library.set("hold_speed", rate)
        self.toast.flash(f"Hold-click speed {rate:g}×")

    def _open_tracks_menu(self) -> None:
        menu = QMenu(self)
        tracks = self.video_widget.tracks()
        for kind, label, prop in (("audio", "Audio", "aid"), ("sub", "Subtitles", "sid")):
            menu.addSection(label)
            current = self.video_widget.mpv_get(prop)
            entries = [t for t in tracks if t.get("type") == kind]
            if not entries:
                empty = menu.addAction(f"No {label.lower()} tracks")
                empty.setEnabled(False)
            for track in entries:
                name = track.get("title") or track.get("lang") or f"Track {track.get('id')}"
                codec = track.get("codec") or ""
                action = menu.addAction(f"{name}  ·  {codec}" if codec else name)
                action.setCheckable(True)
                action.setChecked(track.get("id") == current)
                action.triggered.connect(
                    lambda _checked=False, p=prop, i=track.get("id"): self._set_track(p, i)
                )
            if kind == "sub":
                off = menu.addAction("Off")
                off.setCheckable(True)
                off.setChecked(current in (False, None, "no"))
                off.triggered.connect(lambda: self._set_track("sid", "no"))
                menu.addSeparator()
                load = menu.addAction("Load a subtitle file…")
                load.triggered.connect(self.load_subtitle_file)
                if self.video is not None:
                    if self._transcribing:
                        stop = menu.addAction("Stop generating subtitles")
                        stop.triggered.connect(
                            lambda: self.cancelTranscribeRequested.emit(self.video["id"]))
                    else:
                        make = menu.addAction("Generate subtitles from the audio…")
                        make.triggered.connect(
                            lambda: self.transcribeRequested.emit(self.video))
        self._popup(menu, self.tracks_button)

    def load_subtitle_file(self) -> None:
        start = str(Path(self.video["path"]).parent) if self.video else str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Load a subtitle file", start,
            "Subtitles (*.srt *.ass *.ssa *.sub *.vtt);;All files (*)")
        if chosen:
            self.add_subtitle(chosen)

    def add_subtitle(self, path: str, select: bool = True) -> None:
        """Attach an external subtitle file to what is playing."""
        try:
            self.video_widget.mpv.command("sub-add", path, "select" if select else "auto")
        except Exception as exc:  # noqa: BLE001
            self.toast.flash(f"Could not load {Path(path).name}: {exc}", 3000)
            return
        self.toast.flash(f"Subtitles: {Path(path).name}", 2000)

    def _set_auto_subtitles(self, enabled: bool) -> None:
        self.library.set("auto_subtitles", enabled)
        self.toast.flash("Subtitles will be generated when a video has none" if enabled
                         else "Subtitles will only be generated when you ask")
        if enabled:
            self.maybe_autogenerate()

    def _set_whisper_model(self, name: str) -> None:
        self.library.set("whisper_model", name)
        label = dict((n, l) for n, l, _ in subtitles.MODELS).get(name, name)
        self.toast.flash(f"Subtitle model: {label}")

    def maybe_autogenerate(self) -> None:
        """Ask for generated subtitles if this video has none and the user wants them."""
        if self.video is None or self._transcribing:
            return
        if not self.library.get("auto_subtitles", False):
            return
        if self.has_subtitle_track() or subtitles.existing_subtitle(self.video["path"]):
            return
        self.transcribeRequested.emit(self.video)

    def _set_subtitle_language(self, code: str) -> None:
        self.library.set("subtitle_language", code)
        self._apply_subtitle_preferences()
        label = dict(SUBTITLE_LANGUAGES).get(code, code)
        self.toast.flash(f"Preferred subtitle language: {label}")

    def _set_track(self, prop: str, value) -> None:
        self.video_widget.mpv_set(prop, value)
        self.toast.flash("Track changed", 900)

    def _open_settings_menu(self) -> None:
        menu = QMenu(self)
        for key, label, default in (
            ("autoplay_next", "Autoplay next video", True),
            ("resume_playback", "Resume where I left off", True),
            ("hardware_decoding", "Hardware decoding (restart to apply)", False),
        ):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(self.library.get(key, default)))
            action.toggled.connect(lambda checked, k=key: self.library.set(k, checked))
        auto = menu.addAction("Generate subtitles when a video has none")
        auto.setCheckable(True)
        auto.setChecked(bool(self.library.get("auto_subtitles", False)))
        auto.toggled.connect(self._set_auto_subtitles)

        models = menu.addMenu("Subtitle model")
        current_model = str(self.library.get("whisper_model", subtitles.DEFAULT_MODEL))
        for name, label, size in subtitles.MODELS:
            action = models.addAction(f"{label}   ({size})")
            action.setCheckable(True)
            action.setChecked(name == current_model)
            action.triggered.connect(lambda _c=False, n=name: self._set_whisper_model(n))

        languages = menu.addMenu("Preferred subtitle language")
        current_language = str(self.library.get("subtitle_language", "en"))
        for code, label in SUBTITLE_LANGUAGES:
            action = languages.addAction(label)
            action.setCheckable(True)
            action.setChecked(code == current_language)
            action.triggered.connect(
                lambda _checked=False, c=code: self._set_subtitle_language(c))
        menu.addSeparator()
        watched = menu.addAction("Mark as watched  (W)")
        watched.triggered.connect(self.toggle_watched)
        shot = menu.addAction("Save frame as image  (S)")
        shot.triggered.connect(self.take_screenshot)
        menu.addSeparator()
        shortcuts = menu.addAction("Keyboard shortcuts  (?)")
        shortcuts.triggered.connect(self.helpRequested)
        self._popup(menu, self.settings_button)

    def _popup(self, menu: QMenu, anchor: QWidget) -> None:
        self._controls_locked = True
        self.wake_chrome(sticky=True)
        menu.aboutToHide.connect(self._release_menu)
        origin = anchor.mapToGlobal(anchor.rect().topLeft())
        menu.exec(origin - QPoint(0, menu.sizeHint().height() + 6))

    def _release_menu(self) -> None:
        self._controls_locked = False
        self.wake_chrome()

    # ------------------------------------------------------------- shortcuts

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        step = 60 if shift else 5

        if key in (Qt.Key.Key_Space, Qt.Key.Key_K):
            self.toggle_pause()
        elif key == Qt.Key.Key_Left:
            self.skip(-step)
        elif key == Qt.Key.Key_Right:
            self.skip(step)
        elif key == Qt.Key.Key_J:
            self.skip(-10)
        elif key == Qt.Key.Key_L:
            self.skip(10)
        elif key == Qt.Key.Key_Up:
            self.adjust_volume(5)
        elif key == Qt.Key.Key_Down:
            self.adjust_volume(-5)
        elif key == Qt.Key.Key_M:
            self.toggle_mute()
        elif key == Qt.Key.Key_F:
            self.set_fullscreen(not self._is_fullscreen)
        elif key == Qt.Key.Key_Escape:
            if self._is_fullscreen:
                self.set_fullscreen(False)
            else:
                self.backRequested.emit()
        elif key == Qt.Key.Key_Backspace:
            self.restart()
        elif key == Qt.Key.Key_BracketLeft:
            self.nudge_speed(-1)
        elif key == Qt.Key.Key_BracketRight:
            self.nudge_speed(1)
        elif key == Qt.Key.Key_Backslash:
            self.set_speed(1.0)
        elif key == Qt.Key.Key_Comma:
            self.video_widget.frame_step(False)
        elif key == Qt.Key.Key_Period:
            self.video_widget.frame_step(True)
        elif key == Qt.Key.Key_Home:
            self.video_widget.seek_absolute(0)
        elif key == Qt.Key.Key_End and self._duration:
            self.video_widget.seek_absolute(max(0.0, self._duration - 3))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self.video_widget.seek_percent((key - Qt.Key.Key_0) * 10)
            self.wake_chrome()
        elif key == Qt.Key.Key_V:
            self.toast.flash(self.video_widget.cycle_track("sub") or "No subtitles")
        elif key == Qt.Key.Key_A:
            self.toast.flash(self.video_widget.cycle_track("audio") or "No audio tracks")
        elif key == Qt.Key.Key_Z:
            self.toast.flash(f"Subtitle delay {self.video_widget.set_subtitle_delay(-0.1):+.1f}s")
        elif key == Qt.Key.Key_X:
            self.toast.flash(f"Subtitle delay {self.video_widget.set_subtitle_delay(0.1):+.1f}s")
        elif key == Qt.Key.Key_PageDown:
            self.next_chapter()
        elif key == Qt.Key.Key_PageUp:
            self.previous_chapter()
        elif key == Qt.Key.Key_N:
            self.stepRequested.emit(1)
        elif key == Qt.Key.Key_P:
            self.stepRequested.emit(-1)
        elif key == Qt.Key.Key_W:
            self.toggle_watched()
        elif key == Qt.Key.Key_S:
            self.take_screenshot()
        elif key in (Qt.Key.Key_Question, Qt.Key.Key_F1):
            self.helpRequested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
