"""Compact mode: the player, and nothing else.

A small window that floats above whatever you are working in — the sleeve,
what is playing, how far through it is, and the three buttons that matter.
The main window steps aside while it is up, so this is a mode rather than a
second window to keep track of: the same player, the same queue, the same
scrobbles, just less of the furniture.

Leaving it (the expand button, or closing the window) brings the main window
back exactly as it was.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from .icons import icon
from .widgets import ElidedLabel, SeekBar, format_time

COVER = 76                     # the sleeve, square
ROUND = 8
WIDTH = 430                    # what it opens at
HEIGHT = 112


def _flat(name: str, tip: str, size: int) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size))
    button.setIconSize(QSize(size, size))
    button.setFixedSize(size + 14, size + 14)
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def _rounded(pixmap: QPixmap, side: int) -> QPixmap:
    scaled = pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, side, side, ROUND, ROUND)
    painter.setClipPath(path)
    painter.drawPixmap(int((side - scaled.width()) / 2),
                       int((side - scaled.height()) / 2), scaled)
    painter.end()
    return out


class MiniPlayer(QWidget):
    """The whole player in a window the size of a notification."""

    expandRequested = Signal()

    def __init__(self, player, covers, parent=None):
        super().__init__(None)             # its own window, not a child
        self.player = player
        self.covers = covers
        self.setObjectName("MiniPlayer")
        self.setWindowTitle("Aeris")
        # Above the work, out of the task switcher's way, and never resized
        # into something that is no longer compact.
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(WIDTH)
        self.setFixedHeight(HEIGHT)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(14)

        self.cover = QLabel()
        self.cover.setObjectName("MiniCover")
        self.cover.setFixedSize(COVER, COVER)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.cover)

        side = QVBoxLayout()
        side.setContentsMargins(0, 2, 0, 2)
        side.setSpacing(2)
        names = QHBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = ElidedLabel("Nothing playing")
        self.title.setObjectName("MiniTitle")
        self.subtitle = ElidedLabel("")
        self.subtitle.setObjectName("MiniSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        names.addLayout(titles, 1)
        self.expand = _flat("fullscreen", "Back to the window  (Ctrl+M)", 16)
        self.expand.clicked.connect(self.expandRequested)
        names.addWidget(self.expand, 0, Qt.AlignmentFlag.AlignTop)
        side.addLayout(names)
        side.addStretch(1)

        self.seek = SeekBar()
        self.seek.seeked.connect(player.seek)
        side.addWidget(self.seek)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("MiniTime")
        self.total = QLabel("0:00")
        self.total.setObjectName("MiniTime")
        self.previous = _flat("prev", "Previous", 16)
        self.previous.clicked.connect(player.previous_track)
        self.play = _flat("play", "Play / pause", 20)
        self.play.clicked.connect(player.toggle_pause)
        self.next = _flat("next", "Next", 16)
        self.next.clicked.connect(player.next_track)
        row.addWidget(self.elapsed)
        row.addStretch(1)
        for button in (self.previous, self.play, self.next):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.total)
        side.addLayout(row)
        outer.addLayout(side, 1)

        player.trackChanged.connect(self._on_track)
        player.positionChanged.connect(self._on_position)
        player.pausedChanged.connect(self._on_paused)
        self._on_track(player.current or {})
        self._on_paused(player.paused)

    # ------------------------------------------------------------- the song

    def _on_track(self, track: dict) -> None:
        if not track:
            self.title.setText("Nothing playing")
            self.subtitle.setText("")
            self.cover.clear()
            self.seek.set_duration(0)
            self.seek.set_position(0)
            return
        self.title.setText(track.get("title") or "")
        self.subtitle.setText(track.get("artist") or track.get("album_artist") or "")
        self.seek.set_duration(float(track.get("duration") or 0))
        from .music_player import album_cover_source     # noqa: PLC0415
        pixmap = self.covers.get(album_cover_source({
            "album": track.get("album"), "album_artist": track.get("album_artist"),
            "sample_path": track.get("path"), "tracks": 1}))
        self.cover.setPixmap(_rounded(pixmap, COVER) if not pixmap.isNull()
                             else QPixmap())

    def _on_position(self, position: float, duration: float) -> None:
        if not self.isVisible():
            return                      # nothing to show while it is put away
        self.elapsed.setText(format_time(position))
        if duration:
            self.total.setText(format_time(duration))
            self.seek.set_duration(duration)
        self.seek.set_position(position)

    def _on_paused(self, paused: bool) -> None:
        self.play.setIcon(icon("play" if paused else "pause", 20))

    # ------------------------------------------------------------- the window

    def place(self) -> None:
        """Bottom right of the screen the pointer is on, the first time."""
        screen = self.screen()
        if screen is None:
            return
        room = screen.availableGeometry()
        self.resize(WIDTH, HEIGHT)
        self.move(room.right() - WIDTH - 28, room.bottom() - HEIGHT - 28)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._on_track(self.player.current or {})
        self._on_paused(self.player.paused)

    def closeEvent(self, event) -> None:
        """Closing compact mode is leaving it, not leaving Aeris."""
        event.ignore()
        self.expandRequested.emit()
