"""The full-screen music stage: the record on one side, the words on the other.

Entered from the now-playing bar (or F on the Music tab), Esc leaves.

The shape of it is the one the good lyric views have settled on. The left is
the record itself — cover, progress, title, artist, transport — and the right
is the song's words, left-aligned and large. Behind both is a photograph of the
artist, blurred until nothing of it is left but its light and colour, and
fading across when the artist changes. Where no photo can be found, the
cover's own colours stand in: the artwork blown up until only the light
coming off it remains.

The words have a depth of field. The line being sung is sharp and white, the
sung part of it brighter than the part still to come; every other line is
dimmed and softened by how far it is from the voice, so the eye lands in the
right place without being led there. Clicking a line seeks to it.
"""

from PySide6.QtCore import (QEasingCurve, QObject, QRectF, QRunnable, QSize, Qt,
                            QThreadPool, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPixmap)
from PySide6.QtWidgets import (QGraphicsBlurEffect, QGraphicsDropShadowEffect,
                               QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from .artist_art import ArtistArtFetcher
from .icons import icon
from .lyrics import find_lyrics, with_rests
from .music_view import LINE_ROLE, QueuePanel, lyric_nudge
from . import motion
from .widgets import SeekBar, SungLineDelegate, format_time

ROLE = Qt.ItemDataRole.UserRole + 5          # a lyric line's start time

COVER_MIN = 160                              # the record never shrinks past this
COVER_ROUND = 10                             # the corners of the sleeve
_OVERLAY = QColor(6, 8, 14)                  # the dimming wash
BLOOM = 14                                   # the cover, this many pixels across
PHOTO_WIDTH = 360                            # the artist photo is blurred at this size
PHOTO_BLUR = 34                              # … by this much: at full screen that is
                                             # a blur some 250 px across
FADE_MS = 700                                # one artist's light giving way to the next


def _blurred_photo(path: str) -> QPixmap | None:
    """An artist photo, blurred until only its light and colour are left.

    Blurred small and then stretched, which is both cheaper and softer than
    blurring at full size: the stretch is itself a blur. Qt's own blur is
    used (through a scene of one item), and the edges it fades towards
    transparent are cropped away, so the backdrop never shows a dark rim.
    """
    image = QImage(path)
    if image.isNull():
        return None
    small = QPixmap.fromImage(image.scaledToWidth(
        PHOTO_WIDTH, Qt.TransformationMode.SmoothTransformation))
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(small)
    blur = QGraphicsBlurEffect()
    blur.setBlurRadius(PHOTO_BLUR)
    blur.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(blur)
    scene.addItem(item)
    out = QImage(small.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    painter = QPainter(out)
    scene.render(painter, QRectF(out.rect()), QRectF(small.rect()))
    painter.end()
    scene.clear()
    edge = PHOTO_BLUR
    if out.width() <= edge * 3 or out.height() <= edge * 3:
        return QPixmap.fromImage(out)
    return QPixmap.fromImage(out.copy(edge, edge, out.width() - edge * 2,
                                      out.height() - edge * 2))


def _flat(name: str, tip: str, size: int) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size, "#ffffff"))
    button.setIconSize(QSize(size, size))
    motion.press_dip(button)
    button.setFixedSize(size + 18, size + 18)
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


def _rounded(pixmap: QPixmap, side: int) -> QPixmap:
    """The cover, square, with the corners taken off."""
    scaled = pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, side, side, COVER_ROUND, COVER_ROUND)
    painter.setClipPath(path)
    painter.drawPixmap(int((side - scaled.width()) / 2),
                       int((side - scaled.height()) / 2), scaled)
    painter.end()
    return out


class _LyricsSignals(QObject):
    # `object` rather than `list`: see the note in music_view.
    loaded = Signal(int, object, bool)       # track id, lines, synced


class _LyricsJob(QRunnable):
    """Reading one track's lyrics — the tag probe belongs off the UI thread."""

    def __init__(self, track_id: int, path: str, signals: _LyricsSignals):
        super().__init__()
        self.track_id = track_id
        self.path = path
        self.signals = signals

    def run(self) -> None:
        parsed = find_lyrics(self.path)
        self.signals.loaded.emit(self.track_id, with_rests(parsed.lines), parsed.synced)


class MusicStage(QWidget):
    """A full-page view: photo background, karaoke lyrics, transport."""

    exitRequested = Signal()
    fullScreenRequested = Signal()

    QUEUE_WIDTH = 340              # what it opens at, the first time
    QUEUE_MIN = 240

    def __init__(self, player, library, covers, parent=None):
        super().__init__(parent)
        self.player = player
        self.library = library
        self.covers = covers
        self.setObjectName("MusicStage")
        self._bloom: QPixmap | None = None     # the cover, blown up to a wash
        self._bloom_for = ""
        # The artist, photographed and blurred to nothing but light: the
        # backdrop when there is one, the cover's colours when there is not.
        self._photo: QPixmap | None = None
        self._photo_for = ""                   # whose it is
        self._leaving: QPixmap | None = None   # the last backdrop, fading out
        self._fade = 1.0
        self._backdrop: QPixmap | None = None  # the finished background, cached
        self._backdrop_key = None
        self.art_fetcher = ArtistArtFetcher(self)
        self.art_fetcher.ready.connect(self._photo_ready)
        self._fader = QVariantAnimation(self)
        self._fader.setDuration(FADE_MS)
        self._fader.setStartValue(0.0)
        self._fader.setEndValue(1.0)
        self._fader.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._fader.valueChanged.connect(self._fading)
        self._lines: list = []
        self._synced = False
        self._worded = False
        self._lyric_px = 0
        self._cover_for = None
        self._current = -1
        self._track_id = -1
        self._lyrics_cache: dict[int, tuple] = {}
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(60)
        self._reflow_timer.timeout.connect(self._reflow)

        self._lyrics_pool = QThreadPool(self)
        self._lyrics_pool.setMaxThreadCount(1)
        self._lyrics_signals = _LyricsSignals(self)
        self._lyrics_signals.loaded.connect(self._show_lyrics)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 22, 30, 18)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.addStretch(1)
        self.full_button = _flat("fullscreen", "Fill the screen  (F11)", 20)
        self.full_button.setCheckable(True)
        self.full_button.clicked.connect(self.fullScreenRequested)
        top.addWidget(self.full_button, 0, Qt.AlignmentFlag.AlignTop)
        self.queue_button = _flat("queue", "Show what is coming up  (Q)", 20)
        self.queue_button.setCheckable(True)
        self.queue_button.clicked.connect(self._toggle_queue)
        top.addWidget(self.queue_button, 0, Qt.AlignmentFlag.AlignTop)
        self.close = _flat("close", "Leave the stage  (Esc)", 20)
        self.close.clicked.connect(self.exitRequested)
        top.addWidget(self.close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)

        self.lines = QListWidget()
        self.lines.setObjectName("StageLyrics")
        self.lines.setFrameShape(QListWidget.Shape.NoFrame)
        self.lines.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lines.setWordWrap(True)
        # Re-measure every row whenever the width changes, not only when told:
        # rows keep the height they were given, and a narrower list wraps.
        self.lines.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lines.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.lines.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No scrollbar: the song moves the words along, and a bar down the
        # side of them is the one thing the page does not need.
        self.lines.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lines.itemClicked.connect(self._seek_to)
        self.painter = SungLineDelegate(LINE_ROLE, self.lines, centred=False, padding=4)
        self.painter.ink = QColor(255, 255, 255)
        self.painter.glow = QColor(255, 255, 255, 46)
        self.painter.blur = 1.0        # the stage is where the soft focus lives
        self.lines.setItemDelegate(self.painter)

        # The same queue as the library view, so it behaves the same way here:
        # click to jump, drag to reorder, Clear to empty it.
        self.queue = QueuePanel(player, self)
        self.queue.setObjectName("StageQueue")
        self.queue.setMinimumWidth(self.QUEUE_MIN)
        self.queue.hide()

        # The record itself, and everything that belongs to it: the sleeve,
        # how far through we are, what it is called and the transport. One
        # column, so the words on the right have the page to themselves.
        self.cover = QLabel()
        self.cover.setObjectName("StageCover")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setMinimumSize(COVER_MIN, COVER_MIN)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 14)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.cover.setGraphicsEffect(shadow)

        self.side = QWidget()
        self.side.setObjectName("StageSide")
        self.side.setMinimumWidth(COVER_MIN)
        self.side.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        column = QVBoxLayout(self.side)
        column.setContentsMargins(4, 4, 4, 4)
        column.setSpacing(0)
        column.addStretch(1)
        column.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(18)

        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("StageTime")
        self.total = QLabel("0:00")
        self.total.setObjectName("StageTime")
        self.seek = SeekBar()
        self.seek.setMinimumWidth(120)
        self.seek.seeked.connect(player.seek)
        self.timing = QWidget()        # kept to the width of the sleeve
        timing = QHBoxLayout(self.timing)
        timing.setContentsMargins(0, 0, 0, 0)
        timing.setSpacing(10)
        timing.addWidget(self.elapsed)
        timing.addWidget(self.seek, 1)
        timing.addWidget(self.total)
        column.addWidget(self.timing, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(14)

        self.title = QLabel("Nothing playing")
        self.title.setObjectName("StageTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("StageSubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self.title)
        column.addWidget(self.subtitle)
        column.addSpacing(10)

        self.previous = _flat("prev", "Previous", 18)
        self.previous.clicked.connect(player.previous_track)
        self.play = _flat("play", "Play / pause  (Space)", 26)
        self.play.clicked.connect(player.toggle_pause)
        self.next = _flat("next", "Next", 18)
        self.next.clicked.connect(player.next_track)
        transport = QHBoxLayout()
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(6)
        transport.addStretch(1)
        for button in (self.previous, self.play, self.next):
            transport.addWidget(button)
        transport.addStretch(1)
        column.addLayout(transport)
        column.addStretch(1)

        self.panes = QSplitter(Qt.Orientation.Horizontal)
        self.panes.setObjectName("StagePanes")
        self.panes.setChildrenCollapsible(False)
        self.panes.setHandleWidth(5)
        self.panes.addWidget(self.side)
        self.panes.addWidget(self.lines)
        self.panes.addWidget(self.queue)
        self.panes.setStretchFactor(0, 0)
        self.panes.setStretchFactor(1, 1)
        self.panes.setStretchFactor(2, 0)
        self.panes.splitterMoved.connect(self._panes_moved)

        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.addStretch(1)
        middle.addWidget(self.panes, 8)
        middle.addStretch(1)
        outer.addLayout(middle, 4)

        player.trackChanged.connect(self._on_track)
        player.positionChanged.connect(self._on_position)
        player.pausedChanged.connect(self._on_paused)
        self._on_paused(player.paused)
        self._restore_queue()

    LYRIC_MIN, LYRIC_MAX = 26, 72
    LYRIC_PER_WIDTH = 0.052        # of the width the words have
    LYRIC_PER_HEIGHT = 0.062       # of the page's height, so a wide short window
                                   # still shows a good few lines at once

    def _lyric_size(self) -> int:
        """Big, and bigger on a bigger screen — the words are the page.

        Sized off the column the words actually get, and the height of the
        page. It used to go off the whole page's width with a ceiling of 46 px,
        which a 1280 px window already reached: full screen on a large display
        got the same small print as a window.
        """
        wide = self.lines.width() or self.width() or 900
        tall = self.height() or 700
        size = min(wide * self.LYRIC_PER_WIDTH, tall * self.LYRIC_PER_HEIGHT)
        return int(max(self.LYRIC_MIN, min(size, self.LYRIC_MAX)))

    GAP_SHARE, GAP_MIN, GAP_MAX = 0.04, 32, 96

    def _gap(self) -> int:
        """Air between the record and the words, in proportion to the page."""
        return int(max(self.GAP_MIN, min(self.width() * self.GAP_SHARE, self.GAP_MAX)))

    def _sleeve_side(self) -> int:
        """How big the record can be: the column's width, or the height it
        can spare once the name and the transport underneath have had theirs."""
        margins = self.side.layout().contentsMargins()
        wide = self.side.width() - margins.left() - margins.right() - 8
        tall = self.side.height() - 190
        return max(COVER_MIN, min(wide, tall if tall > COVER_MIN else wide))

    def _draw_cover(self) -> None:
        """Fit the record to the room the column gave it, corners and all."""
        track = self._cover_for
        side = self._sleeve_side()
        if not track or side < 40:
            return
        from .music_player import album_cover_source   # noqa: PLC0415
        source = album_cover_source({
            "album": track.get("album"), "album_artist": track.get("album_artist"),
            "sample_path": track.get("path"), "tracks": 1})
        pixmap = self.covers.get(source)
        if pixmap.isNull():
            return
        self._set_bloom(pixmap, source)
        self.cover.setFixedSize(side, side)
        self.cover.setPixmap(_rounded(pixmap, side))
        self.timing.setFixedWidth(side)

    def _resize_lyrics(self) -> None:
        # A stylesheet, not setFont: the theme puts a font-size on every
        # QWidget and a stylesheet always wins over one set in code.
        size = self._lyric_size()
        if size == self._lyric_px:
            return
        self._lyric_px = size
        self.lines.setStyleSheet(
            f"QListWidget#StageLyrics {{ font-size: {size}px; font-weight: 700; }}")
        self.lines.doItemsLayout()

    @property
    def word_sync(self) -> bool:
        return bool(self.library.get("word_sync", True))

    def set_word_sync(self, on: bool) -> None:
        self.painter.enabled = self._worded and on
        self.lines.viewport().update()

    # ------------------------------------------------------------- the queue

    def toggle_queue(self) -> None:
        self.queue_button.setChecked(not self.queue_button.isChecked())
        self._toggle_queue()

    def _toggle_queue(self) -> None:
        showing = self.queue_button.isChecked()
        if self.queue.isVisible() and not showing:
            self._remember_queue()
        self.queue.setVisible(showing)
        self.library.set("stage_queue", showing)
        # After the splitter has taken the change in, not in the middle of it.
        QTimer.singleShot(0, self._lay_out_queue)

    LYRICS_MIN = 320               # the words never get less than this

    def queue_width(self, room: int) -> int:
        """How wide the queue should be, given the room there is for both."""
        try:
            wanted = int(self.library.get("stage_queue_width", self.QUEUE_WIDTH))
        except (TypeError, ValueError):
            wanted = self.QUEUE_WIDTH
        most = max(self.QUEUE_MIN, room - self.LYRICS_MIN)
        return max(self.QUEUE_MIN, min(wanted, most))

    def _cover_width(self, room: int) -> int:
        """The record's column: about a third of the page, never squeezed
        below a sleeve's worth, never wider than the page is tall."""
        tallest = (self.panes.height() or COVER_MIN) + 40
        return max(COVER_MIN + 40, min(int(room * 0.34), tallest))

    def _lay_out_queue(self) -> None:
        room = self.panes.width()
        if room <= 0:
            return
        if not self.queue.isVisible():
            art = self._cover_width(room)
            self.panes.setSizes([art, room - art, 0])
            self._draw_cover()
            return
        wide = self.queue_width(room)
        art = self._cover_width(room - wide)
        self.panes.setSizes([art, room - wide - art, wide])
        self._draw_cover()
        self.lines.doItemsLayout()

    def _panes_moved(self, *_args) -> None:
        self._remember_queue()
        # The lines wrap — and are sized — to the width they are given, and a
        # list does not work that out again on its own.
        self._resize_lyrics()
        self._draw_cover()
        self.lines.doItemsLayout()
        self._recentre()

    def _remember_queue(self) -> None:
        if self.queue.isVisible() and self.queue.width() >= self.QUEUE_MIN:
            self.library.set("stage_queue_width", self.queue.width())

    def _restore_queue(self) -> None:
        self.queue_button.setChecked(bool(self.library.get("stage_queue", False)))
        self.queue.setVisible(self.queue_button.isChecked())

    def set_full(self, full: bool) -> None:
        """Keep the button in step with the window it belongs to."""
        self.full_button.setChecked(full)
        self.full_button.setIcon(
            icon("fullscreen_exit" if full else "fullscreen", 20, "#ffffff"))
        self.full_button.setToolTip(
            "Back to a window  (F11)" if full else "Fill the screen  (F11)")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # After the layout has settled, not during it: asked now, the splitter
        # would still be reporting whatever width it had before being shown.
        QTimer.singleShot(0, self._lay_out_queue)
        QTimer.singleShot(0, self._resize_lyrics)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._resize_lyrics()
        # Everything else waits for the resizing to settle: a window being
        # dragged sends dozens of these, and laying out the whole song for
        # each one would make the drag stutter.
        self._reflow_timer.start()

    def _reflow(self) -> None:
        """Fit the page to its new size: the record, the columns, and the
        words, which wrap differently at a new width — then find the line
        being sung again, which the rewrap has moved."""
        if not self.isVisible():
            return
        margins = self.side.layout().contentsMargins()
        self.side.layout().setContentsMargins(margins.left(), margins.top(),
                                              self._gap(), margins.bottom())
        self._lay_out_queue()
        # The type is sized now, after the columns have their new widths. It
        # used to be sized in the resize itself, against the old width, and
        # every row measured then kept a height for words it no longer fitted
        # — a line that now wrapped spilled into the next one.
        self._resize_lyrics()
        self.lines.doItemsLayout()
        self._recentre()

    def _recentre(self) -> None:
        if 0 <= self._current < self.lines.count():
            self.lines.scrollToItem(self.lines.item(self._current),
                                    QListWidget.ScrollHint.PositionAtCenter)

    # ------------------------------------------------------------------ track

    def _on_track(self, track: dict) -> None:
        if not track:
            self._track_id = -1
            self.title.setText("Nothing playing")
            self.subtitle.setText("")
            self._set_lines([], False)
            self.seek.set_duration(0)
            self.seek.set_position(0)
            self._cover_for = None
            self.cover.clear()
            return
        self._track_id = track["id"]
        self._cover_for = track
        self._draw_cover()
        self.title.setText(track.get("title") or "")
        bits = [track.get("artist") or track.get("album_artist") or "",
                track.get("album") or ""]
        self.subtitle.setText("  ·  ".join(b for b in bits if b))
        self.seek.set_duration(float(track.get("duration") or 0))
        artist = (track.get("album_artist") or track.get("artist") or "").strip()
        if artist and artist != self._photo_for:
            self.art_fetcher.fetch(artist)     # answered at once when cached

        cached = self._lyrics_cache.get(track["id"])
        if cached is not None:
            self._set_lines(*cached)
        else:
            self._set_lines([], False)
            self.lines.addItem(QListWidgetItem("  Looking for lyrics…"))
            self._lyrics_pool.start(
                _LyricsJob(track["id"], track["path"], self._lyrics_signals))

    def _on_position(self, position: float, duration: float) -> None:
        self.elapsed.setText(format_time(position))
        if duration:
            self.total.setText(format_time(duration))
            self.seek.set_duration(duration)
        self.seek.set_position(position)
        self._follow(position)

    def _on_paused(self, paused: bool) -> None:
        self.play.setIcon(icon("play" if paused else "pause", 26, "#ffffff"))

    # ----------------------------------------------------------------- lyrics

    def forget_lyrics(self) -> None:
        """Read them again next time: the files on disk have changed."""
        self._lyrics_cache.clear()
        if self.player.current:
            self._on_track(self.player.current)

    def _show_lyrics(self, track_id: int, lines: list, synced: bool) -> None:
        self._lyrics_cache[track_id] = (lines, synced)
        if track_id == self._track_id:
            self._set_lines(lines, synced)

    def _set_lines(self, lines: list, synced: bool) -> None:
        self._lines = list(lines)
        self._synced = synced
        self._current = -1
        self.painter.start_over(synced)
        self.lines.clear()
        if not lines:
            note = QListWidgetItem("  No lyrics for this song.")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setForeground(QColor(255, 255, 255, 130))
            self.lines.addItem(note)
            return
        self._worded = any(getattr(line, "words", ()) for line in lines)
        self.painter.enabled = self._worded and self.word_sync
        for line in lines:
            item = QListWidgetItem("")
            item.setData(ROLE, line[0])
            item.setData(LINE_ROLE, line)
            self.lines.addItem(item)
        self._lyric_px = 0                 # make the next sizing take effect
        self._resize_lyrics()

    def _follow(self, position: float) -> None:
        if not self._synced or not self._lines:
            return
        position += lyric_nudge(self.library)
        index = -1
        for row, (start, _text) in enumerate(self._lines):
            if start is not None and start <= position + 0.05:
                index = row
            else:
                break
        moved = index != self._current
        self._current = index
        self.painter.follow(index, position)
        if self._worded:
            self.lines.viewport().update()   # the words move within the line
        if not moved:
            return
        self.lines.viewport().update()
        if 0 <= index < self.lines.count():
            self.lines.scrollToItem(self.lines.item(index),
                                    QListWidget.ScrollHint.PositionAtCenter)

    def _seek_to(self, item: QListWidgetItem) -> None:
        start = item.data(ROLE)
        if start is not None:
            self.player.seek(float(start))

    # ------------------------------------------------------------------ bloom

    def _set_bloom(self, pixmap: QPixmap, source) -> None:
        """Keep the cover, shrunk to almost nothing, as the page's colour.

        Fourteen pixels across is small enough that no edge of the picture
        survives being blown back up — what is left is where its light and
        its colour sit, which is what the background wants to be.
        """
        key = str(source)
        if key == self._bloom_for and self._bloom is not None:
            return
        self._bloom_for = key
        self._bloom = pixmap.scaled(BLOOM, BLOOM,
                                    Qt.AspectRatioMode.IgnoreAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
        self.update()

    # ------------------------------------------------------------------ paint

    # --------------------------------------------------------------- backdrop

    def _photo_ready(self, artist: str, path: str) -> None:
        track = self.player.current or {}
        playing = (track.get("album_artist") or track.get("artist") or "").strip()
        if artist != playing or artist == self._photo_for:
            return                     # for a song that has moved on, or already up
        self._photo_for = artist
        photo = _blurred_photo(path) if path else None
        self._cross_to(photo)

    def _cross_to(self, photo: QPixmap | None) -> None:
        """Fade from whatever is behind the words now to `photo` (or, with
        no photo, to the cover's colours)."""
        self._leaving = self._photo if self._photo is not None else self._bloom
        self._photo = photo
        if self._leaving is None or not self.isVisible():
            self._fade = 1.0
            self._leaving = None
            self._backdrop_key = None
            self.update()
            return
        self._fader.stop()
        self._fader.start()

    def _fading(self, value) -> None:
        self._fade = float(value)
        if self._fade >= 1.0:
            self._leaving = None
        self.update()

    @staticmethod
    def _fill(painter: QPainter, rect, picture: QPixmap) -> None:
        """Cover `rect` with `picture`, cropping rather than squashing."""
        source = QRectF(picture.rect())
        target = QRectF(rect)
        scale = max(target.width() / source.width(), target.height() / source.height())
        width, height = target.width() / scale, target.height() / scale
        source = QRectF(source.center().x() - width / 2, source.center().y() - height / 2,
                        width, height)
        painter.drawPixmap(target, picture, source)

    def _compose(self, painter: QPainter, rect) -> None:
        painter.fillRect(rect, QColor("#0b0d14"))
        showing = self._photo if self._photo is not None else self._bloom
        if self._leaving is not None and self._fade < 1.0:
            self._fill(painter, rect, self._leaving)
            painter.setOpacity(self._fade)
        if showing is not None and not showing.isNull():
            # The cover's fourteen pixels, or the blurred photograph: all
            # light and colour, no picture either way.
            self._fill(painter, rect, showing)
        painter.setOpacity(1.0)
        # Darkened towards the edges, so white words sit on it at any volume
        # of colour the backdrop happens to have — a photo more than a cover.
        deep = 30 if self._photo is not None else 0
        wash = QLinearGradient(0, 0, 0, rect.height())
        tint = _OVERLAY
        wash.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), 170 + deep))
        wash.setColorAt(0.45, QColor(tint.red(), tint.green(), tint.blue(), 120 + deep))
        wash.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 185 + deep))
        painter.fillRect(rect, wash)

    def paintEvent(self, _event) -> None:
        """The backdrop. Kept as one finished picture between changes: the
        words repaint many times a second over it, and stretching a photo to
        the whole screen each time would be most of the work."""
        painter = QPainter(self)
        rect = self.rect()
        if self._leaving is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._compose(painter, rect)           # mid-fade: every frame differs
            painter.end()
            return
        key = (rect.size().toTuple(), id(self._photo), id(self._bloom))
        if key != self._backdrop_key or self._backdrop is None:
            ratio = self.devicePixelRatioF()
            self._backdrop = QPixmap(rect.size() * ratio)
            self._backdrop.setDevicePixelRatio(ratio)
            into = QPainter(self._backdrop)
            into.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            self._compose(into, rect)
            into.end()
            self._backdrop_key = key
        painter.drawPixmap(0, 0, self._backdrop)
        painter.end()

    def shutdown(self) -> None:
        self._lyrics_pool.clear()
        self._lyrics_pool.waitForDone(1500)
        self.art_fetcher.shutdown()
