"""The Music tab: albums, artists, songs, playlists, and the now-playing bar."""

import re
import subprocess
from pathlib import Path

from PySide6.QtCore import (QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF,
                            QRunnable, QSize, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QActionGroup, QColor, QFont, QFontMetrics, QKeySequence,
                           QIcon, QPainter, QPainterPath, QPixmap, QShortcut, QStandardItem,
                           QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QSizePolicy, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import motion, style
from .icons import icon
from .library_view import scaled_font
from .lyrics import find_lyrics, with_rests
from .music_player import album_cover_source, album_key
from .widgets import (ElidedLabel, FillingGrid, SeekBar, SungLineDelegate, Toast, VolumeBar,
                      draw_art_ring, draw_play_disc, format_time, glass_menu, typing_now)

ALBUM_ROLE = Qt.ItemDataRole.UserRole + 1
TRACK_ROLE = Qt.ItemDataRole.UserRole + 2

CARD_LEAST = 168               # a cover is never narrower than this
CARD_TEXT = 50                 # the two lines under it
GRID_GAP = 22
GUTTER = 24                    # the page's margin, and the grids land on it
GRID_RIGHT = 0                 # the scroll bar's room takes the rest of it
NAMES_MIN = 150      # what is playing: never narrower than this…
NAMES_MAX = 290      # …and never wide enough to push the window about

TITLE_COLUMN, ARTIST_COLUMN = 2, 3
LINE_ROLE = Qt.ItemDataRole.UserRole + 7   # a lyric line, with its word timings
DEFAULT_SPLIT = 0.65         # of the room they share, this much goes to the title
MIN_NAME = 70                # neither of them is allowed to vanish
SPLITS = (("Much wider titles", 0.80), ("Wider titles", 0.70),
          ("Even", 0.50), ("Wider artists", 0.35))

def _count(number: int, thing: str) -> str:
    return f"{number} {thing}{'' if number == 1 else 's'}"


NUDGE_STEP = 0.1             # how much one press moves the words
NUDGE_RANGE = 3.0            # and how far they may be moved in all


def lyric_nudge(library) -> float:
    """How far ahead of the sound the words run, in seconds.

    Lyrics files are written by hand and often sit a little behind the voice;
    how much is a property of the file, not of the player, so it is yours to
    set. Positive brings the words forward.
    """
    try:
        return max(-NUDGE_RANGE, min(float(library.get("lyric_nudge", 0.0)),
                                     NUDGE_RANGE))
    except (TypeError, ValueError):
        return 0.0


PANE_MIN = 210               # a side panel narrower than this is no use to anyone
LYRICS_WIDTH = 320           # what the lyrics open at, the first time
QUEUE_WIDTH = 300            # and the queue
GRID_EDGE = 8        # air between a grid and the bars above and below


def spoken_total(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


_ALBUM_NUMBER = re.compile(r"#\s*(\d+)|(\d+)(?:st|nd|rd|th)(?:\s+album)?|album\s+(\d+)",
                           re.IGNORECASE)


def album_number(text: str) -> int | None:
    """Which album a search means by position: "#7", "7th", "7th album", "album 7".

    A bare "7" stays an ordinary search, or nobody could look for "1989".
    """
    match = _ALBUM_NUMBER.fullmatch(text.strip())
    return int(next(group for group in match.groups() if group)) if match else None


def _flat_button(name: str, tip: str, size: int = 20) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size, style.TEXT))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedSize(size + 16, size + 16)
    motion.press_dip(button)
    return button


QUEUE_ART = 36                 # the cover beside each song in the queue
PLAY_RADIUS = 21               # the play button drawn on a hovered cover


def play_centre(card: QRect) -> QPointF:
    """Where a card's play button sits: the middle of its cover. The
    delegate draws it here and the grid hit-tests it here, so the picture and
    the thing you can click are always the same place."""
    return QPointF(card.left() + card.width() / 2, card.top() + card.width() / 2)


class AlbumDelegate(QStyledItemDelegate):
    """One album: its cover, and two lines under it. The cover is the card."""

    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.covers = covers
        self.card = QSize(CARD_LEAST, CARD_LEAST + CARD_TEXT)

    def sizeHint(self, _option, _index) -> QSize:
        return self.card

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        album = index.data(ALBUM_ROLE)
        if not album:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        side = option.rect.width()
        art = QRectF(option.rect.left(), option.rect.top(), side, side)
        clip = QPainterPath()
        clip.addRoundedRect(art, style.ART_RADIUS, style.ART_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        pixmap = self.covers.get(album_cover_source(album))
        scaled = pixmap.scaled(art.size().toSize(),
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(QPoint(int(art.left() + (art.width() - scaled.width()) / 2),
                                  int(art.top() + (art.height() - scaled.height()) / 2)),
                           scaled)
        if hovered:
            painter.fillRect(art, QColor(0, 0, 0, 80))
            draw_play_disc(painter, play_centre(option.rect), PLAY_RADIUS)
        painter.restore()
        if selected:
            draw_art_ring(painter, art,
                          bool(option.state & QStyle.StateFlag.State_HasFocus))

        left = option.rect.left()
        title_font = scaled_font(painter.font(), 0.5, QFont.Weight.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(style.TEXT))
        metrics = QFontMetrics(title_font)
        painter.drawText(QRect(left, int(art.bottom()) + 9, side, 20),
                         Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(album["album"], Qt.TextElideMode.ElideRight, side))
        detail_font = scaled_font(title_font, -0.8, QFont.Weight.Normal)
        painter.setFont(detail_font)
        painter.setPen(QColor(style.MUTED))
        bits = [album["album_artist"]]
        if album.get("year"):
            bits.append(str(album["year"]))
        painter.drawText(QRect(left, int(art.bottom()) + 29, side, 18),
                         Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(detail_font).elidedText(
                             "  ·  ".join(b for b in bits if b),
                             Qt.TextElideMode.ElideRight, side))
        painter.restore()


def add_split_actions(menu, current: float, chosen) -> None:
    """The same handful of proportions, wherever they are offered from."""
    group = QActionGroup(menu)
    group.setExclusive(True)
    for label, split in SPLITS:
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(abs(current - split) < 0.03)
        action.setActionGroup(group)
        action.triggered.connect(lambda _checked=False, value=split: chosen(value))
    menu.addSeparator()
    reset = menu.addAction("Back to the usual")
    reset.triggered.connect(lambda: chosen(DEFAULT_SPLIT))


class TrackTable(QTreeWidget):
    """A list of songs, used by every view that shows tracks."""

    playRequested = Signal(list, int)       # tracks, index to start at
    splitChanged = Signal(float)            # the title's share of the names
    queueRequested = Signal(list, bool)     # tracks, play next
    likeToggled = Signal(dict)
    convertRequested = Signal(list)
    lyricsRequested = Signal(list)
    lyricsTimeRequested = Signal(list)
    playlistAddRequested = Signal(list)
    removeRequested = Signal(list)

    def __init__(self, parent=None, show_remove: bool = False):
        super().__init__(parent)
        self.show_remove = show_remove
        self.setObjectName("TrackTable")
        self.setColumnCount(6)
        self.setHeaderLabels(["", "#", "Title", "Artist", "Album", "Time"])
        for column in (1, 5):          # figures, and their headings, on one right edge
            self.headerItem().setTextAlignment(
                column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.header()
        for column in (0, 1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        for column in (TITLE_COLUMN, ARTIST_COLUMN, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        # Title and artist share what is left between them, by a proportion
        # rather than by a fixed width, so the two keep their balance at any
        # window size — and dragging the line between them sets it.
        header.setStretchLastSection(False)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        header.sectionResized.connect(self._section_resized)
        self._split = DEFAULT_SPLIT
        self._adjusting = False
        self.setColumnWidth(0, 30)
        self.setColumnWidth(1, 40)
        self.setColumnWidth(4, 200)
        self.setColumnWidth(5, 64)
        self.itemActivated.connect(self._on_activated)
        self.itemDoubleClicked.connect(self._on_activated)
        self.itemClicked.connect(self._on_clicked)
        self.customContextMenuRequested.connect(self._menu)
        self._tracks: list[dict] = []

    # ------------------------------------------------- how the names divide

    @property
    def split(self) -> float:
        return self._split

    def set_split(self, split: float) -> None:
        """How much of the shared room the title gets, from 0.15 to 0.85."""
        self._split = max(0.15, min(float(split), 0.85))
        self._apply_split()

    def _spare(self) -> int:
        """The room the title and the artist have to divide between them."""
        other = sum(self.columnWidth(column) for column in (0, 1, 4, 5))
        return self.viewport().width() - other

    def _apply_split(self) -> None:
        if self._adjusting:
            return
        spare = self._spare()
        if spare < MIN_NAME * 2:
            return                       # too narrow to divide; leave it be
        artist = max(MIN_NAME, min(int(round(spare * (1.0 - self._split))),
                                   spare - MIN_NAME))
        self._adjusting = True
        self.setColumnWidth(TITLE_COLUMN, spare - artist)
        self.setColumnWidth(ARTIST_COLUMN, artist)
        self._adjusting = False

    def _section_resized(self, index: int, _old: int, new: int) -> None:
        """Dragging the line between them changes the proportion, not the width.

        Letting the title simply grow would push the artist off the end and
        put a scrollbar under the list. What the drag means is "give the title
        more of the share", so that is what it is read as.
        """
        if self._adjusting:
            return
        if index == 4:                   # the album column moved; re-divide the rest
            self._apply_split()
            return
        if index != TITLE_COLUMN:
            return
        spare = self._spare()
        if spare < MIN_NAME * 2:
            return
        self._split = max(0.15, min(new / spare, 0.85))
        self._apply_split()
        self.splitChanged.emit(self._split)

    def _header_menu(self, where: QPoint) -> None:
        menu = QMenu(self)
        label = menu.addAction("Title and artist")
        label.setEnabled(False)
        menu.addSeparator()
        add_split_actions(menu, self._split, self._choose_split)
        menu.exec(self.header().mapToGlobal(where))

    def _choose_split(self, split: float) -> None:
        self.set_split(split)
        self.splitChanged.emit(self._split)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_split()

    # ----------------------------------------------------------------- rows

    def set_tracks(self, tracks: list[dict]) -> None:
        self._tracks = list(tracks)
        self.clear()
        for position, track in enumerate(tracks):
            item = QTreeWidgetItem([
                "", str(track.get("track_no") or ""), track.get("title") or "",
                track.get("artist") or "", track.get("album") or "",
                format_time(track.get("duration") or 0),
            ])
            item.setData(0, TRACK_ROLE, position)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(5, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFont(5, style.figures_font(item.font(5)))
            self.addTopLevelItem(item)
            self._paint_like(item, track)

    def _paint_like(self, item: QTreeWidgetItem, track: dict) -> None:
        liked = bool(track.get("liked"))
        item.setIcon(0, icon("heart_filled" if liked else "heart", 15,
                             style.ACCENT if liked else style.FAINT))

    def refresh_like(self, track_id: int) -> None:
        for position, track in enumerate(self._tracks):
            if track["id"] == track_id:
                item = self.topLevelItem(position)
                if item is not None:
                    self._paint_like(item, track)

    def selected_tracks(self) -> list[dict]:
        positions = sorted(i.data(0, TRACK_ROLE) for i in self.selectedItems())
        return [self._tracks[p] for p in positions if 0 <= p < len(self._tracks)]

    def _on_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        position = item.data(0, TRACK_ROLE)
        if position is not None:
            self.playRequested.emit(self._tracks, int(position))

    def _on_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        position = item.data(0, TRACK_ROLE)
        if position is None:
            return
        track = self._tracks[int(position)]
        self.likeToggled.emit(track)
        self._paint_like(item, track)

    def _menu(self, where: QPoint) -> None:
        tracks = self.selected_tracks()
        if not tracks:
            return
        menu = QMenu(self)
        play = menu.addAction(f"Play {'these' if len(tracks) > 1 else 'this'}")
        play.triggered.connect(lambda: self.playRequested.emit(tracks, 0))
        nxt = menu.addAction("Play next")
        nxt.triggered.connect(lambda: self.queueRequested.emit(tracks, True))
        add = menu.addAction("Add to the queue")
        add.triggered.connect(lambda: self.queueRequested.emit(tracks, False))
        menu.addSeparator()
        liked = all(t.get("liked") for t in tracks)
        like = menu.addAction("Remove from liked songs" if liked else "Like")
        like.triggered.connect(lambda: [self.likeToggled.emit(t) for t in tracks])
        playlist = menu.addAction("Add to a playlist…")
        playlist.triggered.connect(lambda: self.playlistAddRequested.emit(tracks))
        if self.show_remove:
            drop = menu.addAction("Remove from this playlist")
            drop.triggered.connect(lambda: self.removeRequested.emit(tracks))
        menu.addSeparator()
        convert = menu.addAction("Convert to another format…")
        convert.triggered.connect(lambda: self.convertRequested.emit(tracks))
        lyrics = menu.addAction("Fetch lyrics")
        lyrics.triggered.connect(lambda: self.lyricsRequested.emit(tracks))
        timed = menu.addAction("Time the lyrics here…")
        timed.setToolTip("Listen to the song on this machine and put a time to\n"
                         "every word of the lyrics it already has.")
        timed.triggered.connect(lambda: self.lyricsTimeRequested.emit(tracks))
        reveal = menu.addAction("Show in file manager")
        reveal.triggered.connect(
            lambda: subprocess.Popen(["xdg-open", str(Path(tracks[0]["path"]).parent)]))
        copy = menu.addAction("Copy path")
        copy.triggered.connect(lambda: QApplication.clipboard().setText(tracks[0]["path"]))
        menu.exec(self.viewport().mapToGlobal(where))


class AlbumGrid(QWidget):
    """A wall of album covers.

    A click opens an album; a double-click, or the play button on a hovered
    cover, plays it. The play button used to be only a picture and a
    double-click only opened — so playing an album took a right-click and a
    menu. A click waits out the double-click interval before opening, or a
    double-click would open the album and then play it from a page the
    second click never meant to touch. Ctrl and Shift clicks only select, so
    several albums can still be picked and played together.
    """

    albumActivated = Signal(dict)
    albumsPlay = Signal(list)
    albumsMenu = Signal(list, QPoint)

    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.covers = covers
        self.albums: list[dict] = []
        layout = QVBoxLayout(self)
        # The grid keeps half a gap down each side of every cell, which is
        # taken off the margin so its outer edges land on the page's.
        layout.setContentsMargins(GUTTER - GRID_GAP // 2, GRID_EDGE, GRID_RIGHT, 0)
        self.model = QStandardItemModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        delegate = AlbumDelegate(covers, self)
        self.view.setItemDelegate(delegate)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setWrapping(True)
        self.view.setUniformItemSizes(True)
        self._fill = FillingGrid(self.view, delegate, CARD_LEAST, GRID_GAP,
                                 lambda width: width + CARD_TEXT)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(26)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.clicked.connect(self._clicked)
        self.view.doubleClicked.connect(self._play_index)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)  # Enter opens the album with focus
        layout.addWidget(self.view)
        covers.ready.connect(self._cover_ready)
        self._pending = QModelIndex()
        self._ate_press = False        # the play button took a press; its release too
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(lambda: self._activate(self._pending))

    def set_albums(self, albums: list[dict]) -> None:
        self.albums = list(albums)
        self.model.clear()
        for album in self.albums:
            item = QStandardItem()
            item.setData(album, ALBUM_ROLE)
            item.setEditable(False)
            self.model.appendRow(item)

    def go_to(self, position: int) -> dict | None:
        """Bring the album at this place in the wall into view and choose it."""
        if not 0 <= position < len(self.albums):
            self.view.clearSelection()
            return None
        index = self.model.index(position, 0)
        self.view.doItemsLayout()
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        return self.albums[position]

    def selected_albums(self) -> list[dict]:
        return [i.data(ALBUM_ROLE) for i in self.view.selectedIndexes() if i.data(ALBUM_ROLE)]

    def _activate(self, index: QModelIndex) -> None:
        album = index.data(ALBUM_ROLE) if index.isValid() else None
        if album:
            self.albumActivated.emit(album)

    def _clicked(self, index: QModelIndex) -> None:
        if QApplication.keyboardModifiers() & (Qt.KeyboardModifier.ControlModifier
                                               | Qt.KeyboardModifier.ShiftModifier):
            return                          # picking several, not opening one
        self._pending = QModelIndex(index)
        self._open_timer.start(QApplication.styleHints().mouseDoubleClickInterval())

    def _play_index(self, index: QModelIndex) -> None:
        self._open_timer.stop()             # it was a double-click after all
        album = index.data(ALBUM_ROLE)
        if album:
            self.albumsPlay.emit([album])

    def _on_play_button(self, pos: QPoint) -> QModelIndex:
        """The card whose play button is under `pos`, if there is one."""
        index = self.view.indexAt(pos)
        if not index.isValid():
            return QModelIndex()
        centre = play_centre(self.view.visualRect(index))
        reach = PLAY_RADIUS + 3             # a little give at the edge
        dx, dy = pos.x() - centre.x(), pos.y() - centre.y()
        return index if dx * dx + dy * dy <= reach * reach else QModelIndex()

    def eventFilter(self, watched, event) -> bool:
        # Not the view's activated signal: Qt sends that on a double-click as
        # well, so a double-click would play the album and then open it.
        if (watched is self.view and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self._activate(self.view.currentIndex())
            return True
        if watched is self.view.viewport():
            kind = event.type()
            if kind == QEvent.Type.MouseMove:
                over = self._on_play_button(event.position().toPoint()).isValid()
                self.view.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over
                                               else Qt.CursorShape.ArrowCursor)
            elif (kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
                  and event.button() == Qt.MouseButton.LeftButton):
                index = self._on_play_button(event.position().toPoint())
                if index.isValid():
                    # The button plays at once, and the click goes no further:
                    # it neither opens the album nor changes the selection.
                    self._open_timer.stop()
                    self._ate_press = True
                    if kind == QEvent.Type.MouseButtonPress:
                        self._play_index(index)
                    return True
            elif kind == QEvent.Type.MouseButtonRelease and self._ate_press:
                # Let through, the release of a press the view never saw reads
                # to it as a click on whatever it last pressed — and opens it.
                self._ate_press = False
                return True
        return super().eventFilter(watched, event)

    def _menu(self, where: QPoint) -> None:
        albums = self.selected_albums()
        if albums:
            self.albumsMenu.emit(albums, self.view.viewport().mapToGlobal(where))

    def _cover_ready(self, cover_id: int) -> None:
        for row in range(self.model.rowCount()):
            index = self.model.index(row, 0)
            album = index.data(ALBUM_ROLE)
            if album and album_key(album) == cover_id:
                self.model.dataChanged.emit(index, index)
                return


class NowPlayingBar(QWidget):
    """The strip along the bottom: what is playing and how to control it."""

    albumRequested = Signal(str, str)       # album, album artist
    artistRequested = Signal(str)

    def __init__(self, player, covers, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlaying")
        self.player = player
        self.covers = covers
        self._dragging = False

        row = QHBoxLayout(self)
        row.setContentsMargins(GUTTER, 10, GUTTER, 10)
        row.setSpacing(8)

        self.art = QLabel()
        self.art.setFixedSize(52, 52)
        self.art.setObjectName("CoverArt")
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.art)

        # Bounded on purpose. What is playing has to fit the room it is given
        # rather than decide how much room the window needs.
        names = QWidget()
        names.setMinimumWidth(NAMES_MIN)
        names.setMaximumWidth(NAMES_MAX)
        titles = QVBoxLayout(names)
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        self.title = ElidedLabel("Nothing playing")
        self.title.setObjectName("PlayerTitle")
        self.subtitle = ElidedLabel("")
        self.subtitle.setObjectName("PlayerSubtitle")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        row.addWidget(names)

        self.like = _flat_button("heart", "Like this song  (L)", 18)
        self.like.clicked.connect(self._toggle_like)
        row.addWidget(self.like)
        row.addSpacing(8)

        self.shuffle = _flat_button("shuffle", "Shuffle the queue", 19)
        self.shuffle.setCheckable(True)
        self.shuffle.setChecked(player.shuffle)
        self.shuffle.clicked.connect(lambda: player.set_shuffle(self.shuffle.isChecked()))
        row.addWidget(self.shuffle)
        self.previous = _flat_button("prev", "Previous", 20)
        self.previous.clicked.connect(player.previous_track)
        row.addWidget(self.previous)
        self.play = _flat_button("play", "Play / pause  (Space)", 26)
        self.play.clicked.connect(player.toggle_pause)
        row.addWidget(self.play)
        self.next = _flat_button("next", "Next", 20)
        self.next.clicked.connect(player.next_track)
        row.addWidget(self.next)
        self.repeat = _flat_button("repeat", "Repeat: off", 19)
        self.repeat.clicked.connect(self._cycle_repeat)
        row.addWidget(self.repeat)

        row.addSpacing(10)
        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("TimeLabel")
        row.addWidget(self.elapsed)
        self.seek = SeekBar()
        self.seek.setMinimumWidth(180)
        self.seek.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.seek.seeked.connect(player.seek)
        self.seek.scrubStateChanged.connect(self._scrub)
        row.addWidget(self.seek, 1)
        self.total = QLabel("0:00")
        self.total.setObjectName("TimeLabel")
        row.addWidget(self.total)

        row.addSpacing(8)
        self.volume = VolumeBar()
        self.volume.set_value(player.engine.volume)
        self.volume.changed.connect(player.set_volume)
        row.addWidget(self.volume)
        self.lyrics_button = _flat_button("lyrics", "Show the lyrics  (Y)", 19)
        self.lyrics_button.setCheckable(True)
        row.addWidget(self.lyrics_button)
        self.queue_button = _flat_button("queue", "Show the queue  (Q)", 19)
        self.queue_button.setCheckable(True)
        row.addWidget(self.queue_button)
        self.stage_button = _flat_button("fullscreen", "The lyrics stage  (F)", 19)
        row.addWidget(self.stage_button)
        self.compact_button = _flat_button(
            "fullscreen_exit", "Compact mode: just the player  (Ctrl+M)", 19)
        row.addWidget(self.compact_button)

        # Right-click what is playing to go to where it came from.
        for spot in (names, self.art, self.title, self.subtitle):
            spot.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            spot.customContextMenuRequested.connect(
                lambda where, widget=spot: self._track_menu(widget, where))
        self.art.setToolTip("Right-click to go to the album or the artist")

        player.trackChanged.connect(self.show_track)
        player.pausedChanged.connect(self._paused)
        player.positionChanged.connect(self._position)
        player.volumeChanged.connect(self.volume.set_value)
        self._update_repeat()
        # Observers only fire on change, so seed the button from current state.
        self._paused(player.paused)

    def show_track(self, track: dict) -> None:
        if not track:
            self.title.setText("Nothing playing")
            self.subtitle.setText("")
            self.art.clear()
            self.seek.set_duration(0)
            self.seek.set_position(0)
            self.elapsed.setText(format_time(0))
            self.total.setText(format_time(0))
            self._paused(True)
            return
        self.title.setText(track.get("title") or Path(track["path"]).stem)
        bits = [track.get("artist") or "", track.get("album") or ""]
        self.subtitle.setText("  ·  ".join(b for b in bits if b))
        pixmap = self.covers.get(track_cover_source(track))
        self.art.setPixmap(pixmap.scaled(QSize(52, 52), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        self.seek.set_duration(float(track.get("duration") or 0))
        self._refresh_like(track)
        self._paused(self.player.paused)
        QTimer.singleShot(120, lambda: self._paused(self.player.paused))

    def _track_menu(self, widget, where) -> None:
        """Where this song came from, and what else is there."""
        menu = self.track_menu()
        if menu is not None:
            menu.exec(widget.mapToGlobal(where))

    def track_menu(self) -> QMenu | None:
        """The menu itself, built but not shown. None when there is nowhere to go."""
        track = self.player.current
        if not track:
            return None
        menu = QMenu(self)
        album = (track.get("album") or "").strip()
        artist = (track.get("album_artist") or track.get("artist") or "").strip()
        if album:
            go = menu.addAction(f"Go to  {album}")
            go.triggered.connect(lambda: self.albumRequested.emit(
                album, track.get("album_artist") or track.get("artist") or ""))
        if artist:
            go = menu.addAction(f"Go to  {artist}")
            go.triggered.connect(lambda: self.artistRequested.emit(artist))
        if not menu.actions():
            return None                # nothing is tagged; nowhere to go
        menu.addSeparator()
        liked = menu.addAction("Remove from liked" if track.get("liked") else "Like this song")
        liked.triggered.connect(self._toggle_like)
        copy = menu.addAction("Copy path")
        copy.triggered.connect(lambda: QApplication.clipboard().setText(track.get("path", "")))
        return menu

    def _refresh_like(self, track: dict) -> None:
        liked = bool(track.get("liked"))
        self.like.setIcon(icon("heart_filled" if liked else "heart", 18,
                               style.ACCENT if liked else style.TEXT))

    def _toggle_like(self) -> None:
        track = self.player.current
        if track:
            self.player.toggle_liked(track)
            self._refresh_like(track)

    def _paused(self, paused: bool) -> None:
        # With nothing loaded mpv reports itself as unpaused, which had the
        # transport offering to pause silence. The button says what pressing
        # it would do, so with no track it says play.
        playing = self.player.current is not None and not paused
        self.play.setIcon(icon("pause" if playing else "play", 26, style.TEXT))
        self.play.setToolTip("Pause  (Space)" if playing else "Play  (Space)")

    def _position(self, position: float, duration: float) -> None:
        if not self._dragging:
            self.seek.set_position(position)
        if duration:
            self.seek.set_duration(duration)
        self.elapsed.setText(format_time(position))
        self.total.setText(format_time(duration))

    def _scrub(self, active: bool) -> None:
        self._dragging = active

    def _cycle_repeat(self) -> None:
        self.player.cycle_repeat()
        self._update_repeat()

    def _update_repeat(self) -> None:
        mode = self.player.repeat
        name = {"off": "repeat", "all": "repeat", "one": "repeat_one"}[mode]
        colour = style.TEXT if mode == "off" else style.ACCENT
        self.repeat.setIcon(icon(name, 19, colour))
        self.repeat.setToolTip(f"Repeat: {mode}")


def track_cover_source(track: dict) -> dict:
    """The shape CoverCache wants, for the album a song belongs to."""
    return album_cover_source({"album": track.get("album"),
                               "album_artist": track.get("album_artist"),
                               "sample_path": track.get("path"), "tracks": 1})


class QueueDelegate(QStyledItemDelegate):
    """A queue row with its album's cover beside the words.

    A shuffled library is a queue of thousands, rebuilt at every change of
    song, so a cover is only looked for when its row is actually painted, and
    the small copy is kept per album rather than per song.
    """

    def __init__(self, player, covers, parent=None):
        super().__init__(parent)
        self.player = player
        self.covers = covers
        self._icons: dict[int, QIcon] = {}
        blank = QPixmap(QUEUE_ART, QUEUE_ART)
        blank.fill(Qt.GlobalColor.transparent)
        self._blank = QIcon(blank)

    def _option(self, option, index, art: QIcon) -> QStyleOptionViewItem:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
        styled.decorationSize = QSize(QUEUE_ART, QUEUE_ART)
        styled.icon = art
        return styled

    def sizeHint(self, option, index) -> QSize:
        # Measuring a row needs no picture, only the room for one.
        styled = self._option(option, index, self._blank)
        return styled.widget.style().sizeFromContents(
            QStyle.ContentsType.CT_ItemViewItem, styled, QSize(), styled.widget)

    def paint(self, painter, option, index) -> None:
        styled = self._option(option, index, self._icon_for(index))
        styled.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled,
                                          painter, styled.widget)

    def _icon_for(self, index) -> QIcon:
        position = index.data(TRACK_ROLE)
        if position is None or not 0 <= position < len(self.player.queue):
            return self._blank
        source = track_cover_source(self.player.queue[position])
        found = self._icons.get(source["id"])
        if found is None:
            found = self._icons[source["id"]] = self._small(self.covers.get(source))
        return found

    def _small(self, cover: QPixmap) -> QIcon:
        scale = self.parent().devicePixelRatioF()
        side = round(QUEUE_ART * scale)
        small = QPixmap(side, side)
        small.fill(Qt.GlobalColor.transparent)
        painter = QPainter(small)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, side, side), style.ART_RADIUS * scale,
                            style.ART_RADIUS * scale)
        painter.setClipPath(clip)
        fitted = cover.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((side - fitted.width()) // 2, (side - fitted.height()) // 2, fitted)
        painter.end()
        small.setDevicePixelRatio(scale)
        art = QIcon()
        # In every state: left to itself Qt tints a selected row's picture,
        # and the selected row is the song playing.
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
            art.addPixmap(small, mode)
        return art

    def forget(self, cover_id: int) -> bool:
        """A better picture has arrived. Whether this was showing the old one."""
        return self._icons.pop(cover_id, None) is not None


class QueuePanel(QWidget):
    """What is coming up next."""

    def __init__(self, player, covers, parent=None):
        super().__init__(parent)
        self.player = player
        self.setObjectName("Sidebar")
        self.setMinimumWidth(PANE_MIN)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("Queue")
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)
        clear = QPushButton("Clear")
        clear.setObjectName("Flat")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(player.clear_queue)
        header.addWidget(clear)
        column.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("ChapterList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.delegate = QueueDelegate(player, covers, self)
        self.list.setItemDelegate(self.delegate)
        self.list.setIconSize(QSize(QUEUE_ART, QUEUE_ART))
        covers.ready.connect(self._cover_ready)
        # A long song title should fold into the panel's width rather than put
        # a scrollbar under it, now that the panel's width is yours to set.
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(self._activate)
        self.list.itemDoubleClicked.connect(self._activate)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        column.addWidget(self.list, 1)

        player.queueChanged.connect(self.refresh)
        player.trackChanged.connect(lambda _t: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for position, track in enumerate(self.player.queue):
            label = f"{track.get('title') or '?'}\n{track.get('artist') or ''}"
            item = QListWidgetItem(label)
            item.setData(TRACK_ROLE, position)
            if position == self.player.index:
                font = QFont(item.font())
                font.setWeight(QFont.Weight.Bold)
                item.setFont(font)
                item.setForeground(QColor(style.ACCENT))
            self.list.addItem(item)
        if 0 <= self.player.index < self.list.count():
            self.list.setCurrentRow(self.player.index)

    def _cover_ready(self, cover_id: int) -> None:
        if self.delegate.forget(cover_id):
            self.list.viewport().update()

    def _activate(self, item: QListWidgetItem) -> None:
        position = item.data(TRACK_ROLE)
        if position is not None:
            self.player.play_track_at(int(position))

    def _menu(self, where: QPoint) -> None:
        item = self.list.itemAt(where)
        if item is None:
            return
        position = int(item.data(TRACK_ROLE))
        menu = QMenu(self)
        play = menu.addAction("Play this")
        play.triggered.connect(lambda: self.player.play_track_at(position))
        drop = menu.addAction("Remove from the queue")
        drop.triggered.connect(lambda: self.player.remove_at(position))
        menu.exec(self.list.viewport().mapToGlobal(where))


class MusicPage(QWidget):
    """Everything under the Music tab."""

    headingChanged = Signal(str)
    folderAdded = Signal(str)
    rescanRequested = Signal()
    rebuildRequested = Signal()
    glassChanged = Signal(str)          # solid | frosted | clear
    stageRequested = Signal()
    compactRequested = Signal()
    servicesRequested = Signal()
    convertRequested = Signal(list)
    lyricsRequested = Signal(list)
    lyricsRedoRequested = Signal()
    lyricsTimeRequested = Signal(list)
    helpRequested = Signal()
    wordSyncChanged = Signal(bool)

    TABS = [("albums", "Albums", "grid"), ("artists", "Artists", "note"),
            ("songs", "Songs", "list"), ("playlists", "Playlists", "queue"),
            ("sync", "Sync", "sync")]

    def __init__(self, library, player, covers, sync_view, parent=None):
        super().__init__(parent)
        # QWidget subclasses only honour stylesheet backgrounds with this on,
        # which is what makes the theme glass (rgba) actually paint
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.library = library
        self.player = player
        self.covers = covers
        self.sync_view = sync_view
        self.tab = "albums"
        # Every track list, so a change to how the names divide reaches all
        # of them at once rather than only the one that was asked.
        self._tables: list[TrackTable] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # The window hangs these two on its own frame, so that all three
        # libraries share one sidebar and one header.
        self.sidebar = self._build_sidebar()
        self.toolbar = self._build_top_bar()

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_albums())
        self.stack.addWidget(self._build_artists())
        self.stack.addWidget(self._build_songs())
        self.stack.addWidget(self._build_playlists())
        self.stack.addWidget(sync_view)
        self.stack.setMinimumWidth(360)
        self.lyrics_panel = LyricsPanel(player, library, self)
        self.lyrics_panel.hide()
        self.queue_panel = QueuePanel(player, covers, self)
        self.queue_panel.hide()
        # A splitter rather than a row of fixed widths: how much room the
        # words and the queue deserve is a matter of taste, and of how long
        # the song titles are.
        self.panes = QSplitter(Qt.Orientation.Horizontal)
        self.panes.setObjectName("MusicPanes")
        self.panes.setChildrenCollapsible(False)
        self.panes.setHandleWidth(5)
        for index, pane in enumerate((self.stack, self.lyrics_panel, self.queue_panel)):
            self.panes.addWidget(pane)
            self.panes.setStretchFactor(index, 1 if index == 0 else 0)
        self.panes.splitterMoved.connect(lambda *_: self._remember_panes())
        root.addWidget(self.panes, 1)

        self.now_playing = NowPlayingBar(player, covers, self)
        self.now_playing.queue_button.clicked.connect(self._toggle_queue)
        self.now_playing.albumRequested.connect(self.show_album)
        self.now_playing.artistRequested.connect(self.show_artist)
        self.now_playing.lyrics_button.clicked.connect(self._toggle_lyrics)
        self.now_playing.stage_button.clicked.connect(self.stageRequested)
        self.now_playing.compact_button.clicked.connect(self.compactRequested)
        root.addWidget(self.now_playing)

        self.toast = Toast(self, anchor="top")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self.reload)
        player.likedChanged.connect(self._liked_changed)
        # Guarded, every one of them: a bare letter belongs to whatever is
        # being typed into before it belongs to a shortcut.
        QShortcut(QKeySequence("Y"), self,
                  activated=lambda: self.unless_typing(self.toggle_lyrics))
        QShortcut(QKeySequence("Q"), self,
                  activated=lambda: self.unless_typing(self.toggle_queue))
        QShortcut(QKeySequence("Z"), self,
                  activated=lambda: self.unless_typing(lambda: self.nudge_lyrics(NUDGE_STEP)))
        QShortcut(QKeySequence("X"), self,
                  activated=lambda: self.unless_typing(lambda: self.nudge_lyrics(-NUDGE_STEP)))
        # F for the full-screen stage is handled in MainWindow.keyPressEvent,
        # so it cannot shadow the video player's own F shortcut.
        self.set_tab("albums")

    # ------------------------------------------------------------------ build

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.tab_buttons = {}
        for key, label, icon_name in self.TABS:
            button = QPushButton(f"  {label}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setIcon(icon(icon_name, 16, style.MUTED))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _c=False, k=key: self.set_tab(k))
            column.addWidget(button)
            self.tab_buttons[key] = button
        column.addStretch(1)
        # The library pages share this much of an interface with the main window.
        self.status_label = QLabel("")
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(10, 0, 10, 0)
        column.addWidget(self.status_label)
        return panel

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)

        self.search = QLineEdit()
        # Click to search, but do not take the keyboard the moment the
        # app opens: a single-letter shortcut belongs to the music.
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search.setPlaceholderText("Search, or #7 for the 7th album")
        self.search.setToolTip("Songs, albums and artists by name.\n"
                               "#7 or 7th goes to the 7th album on the wall; Enter opens it.")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", 16, style.MUTED),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.search.returnPressed.connect(self._open_numbered_album)
        row.addWidget(self.search)

        rescan = _flat_button("refresh", "Rescan folders", 17)
        rescan.clicked.connect(self.rescanRequested)
        self.rescan_button = rescan
        row.addWidget(rescan)

        add = QPushButton()
        add.setObjectName("Flat")
        add.setIcon(icon("folder_add", 18, style.TEXT))
        add.setFixedSize(34, 34)
        add.setToolTip("Add a music folder")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self.choose_folder)
        row.addWidget(add)

        overflow = QPushButton()
        overflow.setObjectName("Flat")
        overflow.setIcon(icon("more", 18, style.TEXT))
        overflow.setIconSize(QSize(18, 18))
        overflow.setFixedSize(34, 34)
        overflow.setToolTip("More")
        overflow.setCursor(Qt.CursorShape.PointingHandCursor)
        overflow.clicked.connect(lambda: self._open_overflow_menu(overflow))
        row.addWidget(overflow)

        return bar

    def _open_overflow_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        rescan = menu.addAction("Rescan folders")
        rescan.triggered.connect(self.rescanRequested)
        rebuild = menu.addAction("Rebuild library…")
        rebuild.triggered.connect(self.rebuildRequested)
        glass_menu(menu, self.library, self.glassChanged.emit)
        timing = menu.addMenu("Lyrics timing")
        for label, step in (("Earlier  (Z)", NUDGE_STEP), ("Later  (X)", -NUDGE_STEP)):
            timing.addAction(label).triggered.connect(
                lambda _c=False, by=step: self.nudge_lyrics(by))
        timing.addSeparator()
        timing.addAction(f"In step  (now {lyric_nudge(self.library):+.1f}s)"
                         ).triggered.connect(lambda: self.nudge_lyrics(None))
        redo = menu.addAction("Re-download every lyric…")
        redo.setToolTip("Ask again for every song in the library, even the ones\n"
                        "that already have words. Nothing is replaced by a\n"
                        "worse-timed version of itself.")
        redo.triggered.connect(self.lyricsRedoRequested)
        fetch_words = menu.addAction("Look for word-by-word lyrics")
        fetch_words.setCheckable(True)
        fetch_words.setChecked(bool(self.library.get("fetch_word_lyrics", True)))
        fetch_words.setToolTip("Ask NetEase, which times lyrics word by word, before\n"
                               "LRCLIB, which only times them by the line.")
        fetch_words.toggled.connect(
            lambda on: self.library.set("fetch_word_lyrics", bool(on)))
        words = menu.addAction("Light up lyrics word by word")
        words.setCheckable(True)
        words.setChecked(bool(self.library.get("word_sync", True)))
        words.setToolTip("When an .lrc has a time against every word, follow the\n"
                         "words rather than the line. Lyrics timed only by line\n"
                         "are unaffected.")
        words.toggled.connect(self._set_word_sync)
        names = menu.addMenu("Title and artist width")
        names.setToolTip("How the two share the room between them in every "
                         "track list — the same as dragging the line in the header")
        add_split_actions(names, self.name_split, self.set_name_split)
        services = menu.addAction("Scrobbling and Rich Presence…")
        services.triggered.connect(self.servicesRequested)
        menu.addSeparator()
        shortcuts = menu.addAction("Keyboard shortcuts")
        shortcuts.triggered.connect(self.helpRequested)
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _refresh_stats(self) -> None:
        counts = self.library.music_counts()
        self.status_label.setText(
            f"{counts['tracks']} songs · {counts['albums']} albums\n"
            f"{counts['artists']} artists · {spoken_total(counts['seconds'])}")

    def _album_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(GUTTER, 4, GUTTER, 8)
        row.setSpacing(8)
        shuffle = QPushButton("  Shuffle albums")
        shuffle.setObjectName("Primary")
        shuffle.setIcon(icon("shuffle", 17, style.ON_ACCENT))
        shuffle.setCursor(Qt.CursorShape.PointingHandCursor)
        shuffle.setToolTip("Queue every album in a random order, each played in track order")
        shuffle.clicked.connect(self.shuffle_albums)
        row.addWidget(shuffle)
        play_all = QPushButton("  Play all")
        play_all.setIcon(icon("play", 16, style.TEXT))
        play_all.setCursor(Qt.CursorShape.PointingHandCursor)
        play_all.clicked.connect(lambda: self.player.play_albums(self.grid.albums))
        row.addWidget(play_all)
        row.addStretch(1)
        self.album_status = ElidedLabel("")
        self.album_status.setObjectName("HintLabel")
        row.addWidget(self.album_status)
        return bar

    def _build_albums(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.album_stack = QStackedWidget()
        self.grid = AlbumGrid(self.covers)
        self.grid.albumActivated.connect(self.open_album)
        self.grid.albumsPlay.connect(self.player.play_albums)
        self.grid.albumsMenu.connect(self._album_menu)
        # The wall's own actions go when the wall does: an open album has one
        # main action of its own.
        wall = QWidget()
        wall_column = QVBoxLayout(wall)
        wall_column.setContentsMargins(0, 0, 0, 0)
        wall_column.setSpacing(0)
        wall_column.addWidget(self._album_toolbar())
        wall_column.addWidget(self.grid, 1)
        self.album_stack.addWidget(wall)
        self.album_stack.addWidget(self._build_album_detail())
        column.addWidget(self.album_stack, 1)
        return page

    def _build_album_detail(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(GUTTER, 4, GUTTER, 10)
        column.setSpacing(8)
        header = QHBoxLayout()
        back = _flat_button("back", "Back to the albums", 18)
        back.clicked.connect(lambda: self.album_stack.setCurrentIndex(0))
        header.addWidget(back)
        self.detail_title = ElidedLabel("")
        self.detail_title.setObjectName("PageTitle")
        header.addWidget(self.detail_title)
        header.addStretch(1)
        play = QPushButton("  Play album")
        play.setObjectName("Primary")
        play.setIcon(icon("play", 15, style.ON_ACCENT))
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        play.clicked.connect(lambda: self.detail_tracks.playRequested.emit(
            self.detail_tracks._tracks, 0))
        header.addWidget(play)
        column.addLayout(header)
        self.detail_tracks = TrackTable()
        self._wire_table(self.detail_tracks)
        column.addWidget(self.detail_tracks, 1)
        return page

    def _build_artists(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.artist_list = QListWidget()
        self.artist_list.setObjectName("ChapterList")
        self.artist_list.setFixedWidth(232)
        self.artist_list.setContentsMargins(0, 0, 0, 0)
        self.artist_list.setStyleSheet(f"margin-left: {GUTTER - 10}px;")
        self.artist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.artist_list.currentItemChanged.connect(self._artist_selected)
        row.addWidget(self.artist_list)
        right = QVBoxLayout()
        right.setContentsMargins(0, 4, 0, 0)
        right.setSpacing(8)
        head = QHBoxLayout()
        head.setContentsMargins(GUTTER, 0, GUTTER, 0)
        self.artist_title = ElidedLabel("")
        self.artist_title.setObjectName("PageTitle")
        head.addWidget(self.artist_title, 1)
        self.artist_count = QLabel("")
        self.artist_count.setObjectName("HintLabel")
        head.addWidget(self.artist_count)
        play = QPushButton("  Play everything")
        play.setObjectName("Primary")
        play.setIcon(icon("play", 15, style.ON_ACCENT))
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        play.clicked.connect(self._play_artist)
        head.addWidget(play)
        right.addLayout(head)
        # Their albums, not a thousand songs in a row: an artist is a shelf of
        # records, and that is how you look for one of them.
        self.artist_albums = AlbumGrid(self.covers)
        # Opening from here used to fill in the Albums tab and leave you
        # looking at the artist, so nothing seemed to happen. It goes there.
        self.artist_albums.albumActivated.connect(self._open_album_here)
        self.artist_albums.albumsPlay.connect(self.player.play_albums)
        self.artist_albums.albumsMenu.connect(self._album_menu)
        right.addWidget(self.artist_albums, 1)
        holder = QWidget()
        holder.setLayout(right)
        row.addWidget(holder, 1)
        return page

    def _build_songs(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(GUTTER, 4, GUTTER, 10)
        column.setSpacing(8)
        row = QHBoxLayout()
        self.songs_status = ElidedLabel("")
        self.songs_status.setObjectName("HintLabel")
        row.addWidget(self.songs_status)
        row.addStretch(1)
        liked_only = QPushButton("  Liked only")
        liked_only.setObjectName("NavItem")
        liked_only.setCheckable(True)
        liked_only.setIcon(icon("heart", 15, style.MUTED))
        liked_only.setCursor(Qt.CursorShape.PointingHandCursor)
        liked_only.clicked.connect(self.reload)
        self.liked_only = liked_only
        row.addWidget(liked_only)
        self.sort_box = QComboBox()
        for key, label in (("album", "By album"), ("title", "By title"),
                           ("artist", "By artist"), ("added", "Recently added"),
                           ("played", "Recently played"), ("year", "By year")):
            self.sort_box.addItem(label, key)
        self.sort_box.setFixedWidth(160)
        self.sort_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.sort_box)
        column.addLayout(row)
        self.songs = TrackTable()
        self._wire_table(self.songs)
        column.addWidget(self.songs, 1)
        return page

    def _build_playlists(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(GUTTER - 10, 4, 0, 10)
        left.setSpacing(6)
        self.playlist_list = QListWidget()
        self.playlist_list.setObjectName("ChapterList")
        self.playlist_list.setFrameShape(QFrame.Shape.NoFrame)
        self.playlist_list.currentItemChanged.connect(self._playlist_selected)
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlist_list.customContextMenuRequested.connect(self._playlist_menu)
        left.addWidget(self.playlist_list, 1)
        new = QPushButton("  New playlist")
        new.setObjectName("Primary")
        new.setIcon(icon("folder_add", 16, style.ON_ACCENT))
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.clicked.connect(self.new_playlist)
        left.addWidget(new)
        holder = QWidget()
        holder.setFixedWidth(250)
        holder.setLayout(left)
        row.addWidget(holder)

        right = QVBoxLayout()
        right.setContentsMargins(GUTTER, 4, GUTTER, 10)
        right.setSpacing(8)
        head = QHBoxLayout()
        self.playlist_title = QLabel("")
        self.playlist_title.setObjectName("PageTitle")
        head.addWidget(self.playlist_title)
        head.addStretch(1)
        play = QPushButton("  Play")
        play.setIcon(icon("play", 15, style.TEXT))
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        play.clicked.connect(lambda: self.playlist_tracks.playRequested.emit(
            self.playlist_tracks._tracks, 0))
        head.addWidget(play)
        right.addLayout(head)
        self.playlist_tracks = TrackTable(show_remove=True)
        self._wire_table(self.playlist_tracks)
        self.playlist_tracks.removeRequested.connect(self._remove_from_playlist)
        right.addWidget(self.playlist_tracks, 1)
        holder2 = QWidget()
        holder2.setLayout(right)
        row.addWidget(holder2, 1)
        return page

    def nudge_lyrics(self, by: float | None) -> None:
        """Move the words against the sound. None puts them back in step."""
        value = 0.0 if by is None else round(lyric_nudge(self.library) + by, 2)
        value = max(-NUDGE_RANGE, min(value, NUDGE_RANGE))
        self.library.set("lyric_nudge", value)
        self.toast.flash("Lyrics in step with the music" if abs(value) < 0.05 else
                         f"Lyrics {abs(value):.1f}s "
                         f"{'ahead of' if value > 0 else 'behind'} the music")

    def _set_word_sync(self, on: bool) -> None:
        self.library.set("word_sync", bool(on))
        self.lyrics_panel.set_word_sync(on)
        self.wordSyncChanged.emit(bool(on))

    # ------------------------------------------- the lyrics and queue panes

    def _pane_width(self, key: str, fallback: int) -> int:
        try:
            return max(PANE_MIN, int(self.library.get(key, fallback)))
        except (TypeError, ValueError):
            return fallback

    def _remember_panes(self) -> None:
        """Write down how wide each pane was dragged to, while it is open."""
        for pane, key in ((self.lyrics_panel, "music_lyrics_width"),
                          (self.queue_panel, "music_queue_width")):
            if pane.isVisible() and pane.width() >= PANE_MIN:
                self.library.set(key, pane.width())

    def _lay_out_panes(self) -> None:
        """Give each open pane the width it was last left at.

        A splitter forgets a widget's width the moment it is hidden and hands
        the room to its neighbour, so re-opening the queue would otherwise
        always come back at whatever size happened to be left over.
        """
        room = self.panes.width()
        if room <= 0:
            return
        wanted = [0, 0, 0]
        for index, (pane, key, fallback) in enumerate((
                (self.stack, "", 0),
                (self.lyrics_panel, "music_lyrics_width", LYRICS_WIDTH),
                (self.queue_panel, "music_queue_width", QUEUE_WIDTH))):
            if index and pane.isVisible():
                wanted[index] = self._pane_width(key, fallback)
        wanted[0] = max(self.stack.minimumWidth(), room - sum(wanted[1:]))
        self.panes.setSizes(wanted)

    def _show_pane(self, pane, visible: bool) -> None:
        if pane.isVisible() and not visible:
            self._remember_panes()     # keep where it was before it goes
        pane.setVisible(visible)
        self._lay_out_panes()

    def _wire_table(self, table: "TrackTable") -> None:
        table.playRequested.connect(lambda tracks, at: self.player.set_queue(tracks, at))
        table.queueRequested.connect(self._enqueue)
        table.likeToggled.connect(self._like)
        table.convertRequested.connect(self.convertRequested)
        table.lyricsRequested.connect(self.lyricsRequested)
        table.lyricsTimeRequested.connect(self.lyricsTimeRequested)
        table.playlistAddRequested.connect(self.add_to_playlist)
        table.set_split(self.name_split)
        table.splitChanged.connect(self.set_name_split)
        self._tables.append(table)

    # --------------------------------------------- the title/artist balance

    @property
    def name_split(self) -> float:
        """How the title and the artist divide the room they share."""
        try:
            return max(0.15, min(float(self.library.get("music_name_split",
                                                        DEFAULT_SPLIT)), 0.85))
        except (TypeError, ValueError):
            return DEFAULT_SPLIT

    def set_name_split(self, split: float) -> None:
        """Set it once and every list agrees, this session and the next."""
        if abs(split - self.name_split) < 0.005:
            return
        self.library.set("music_name_split", round(float(split), 3))
        for table in self._tables:
            table.set_split(split)

    # ------------------------------------------------------------------- data

    def set_tab(self, key: str) -> None:
        self.tab = key
        self.heading = next(label for k, label, _ in self.TABS if k == key)
        self.headingChanged.emit(self.heading)
        order = [k for k, _, _ in self.TABS]
        self.stack.setCurrentIndex(order.index(key))
        for name, button in self.tab_buttons.items():
            button.setChecked(name == key)
        self.search.setVisible(key not in ("sync",))
        self.reload()

    def reload(self) -> None:
        text = self.search.text().strip()
        number = album_number(text)
        if number is not None and self.tab in ("artists", "songs"):
            self.set_tab("albums")     # reloads, and lands in the branch below
            return
        if self.tab == "albums" and number is not None:
            self._show_numbered_album(number)
        elif self.tab == "albums":
            albums = self.library.albums(search=text)
            self.grid.set_albums(albums)
            # The whole library is already counted in the top bar, so this
            # line only earns its place once a search has narrowed things —
            # and then it has to count what is shown, not what exists.
            if text:
                shown = sum(int(album["tracks"] or 0) for album in albums)
                seconds = sum(float(album["duration"] or 0) for album in albums)
                counts = self.library.music_counts()
                self.album_status.setText(
                    f"{len(albums)} of {counts['albums']} albums · "
                    f"{shown} tracks · {spoken_total(seconds)}")
            else:
                self.album_status.setText("")
        elif self.tab == "artists":
            self._reload_artists(text)
        elif self.tab == "songs":
            tracks = self.library.tracks(search=text, liked=self.liked_only.isChecked(),
                                         sort=self.sort_box.currentData() or "album")
            self.songs.set_tracks(tracks)
            self.songs_status.setText(f"{len(tracks)} songs")
        elif self.tab == "playlists":
            self._reload_playlists()
        elif self.tab == "sync":
            self.sync_view.reload()
        self._refresh_stats()

    def _show_numbered_album(self, number: int) -> None:
        """The whole wall stays up, so what is either side of it can be seen too."""
        albums = self.library.albums()
        self.album_stack.setCurrentIndex(0)
        self.grid.set_albums(albums)
        album = self.grid.go_to(number - 1)
        if album:
            self.album_status.setText(f"Album {number} of {len(albums)} · {album['album']}"
                                      f" · {album['album_artist']}")
        else:
            self.album_status.setText(f"No album {number}: there are {len(albums)}")

    def _open_numbered_album(self) -> None:
        album = None
        number = album_number(self.search.text())
        if number is not None and self.tab == "albums":
            self._search_timer.stop()  # Enter can beat the search's own short wait
            self._show_numbered_album(number)
            album = self.grid.go_to(number - 1)
        if album:
            self.open_album(album)

    def _reload_artists(self, text: str) -> None:
        current = self.artist_list.currentItem()
        chosen = current.text() if current else None
        self.artist_list.blockSignals(True)
        self.artist_list.clear()
        for artist in self.library.artists(search=text):
            item = QListWidgetItem(f"{artist['name']}\n{artist['albums']} albums · "
                                   f"{artist['tracks']} tracks")
            item.setData(TRACK_ROLE, artist["name"])
            self.artist_list.addItem(item)
        self.artist_list.blockSignals(False)
        if self.artist_list.count():
            match = 0
            for row in range(self.artist_list.count()):
                if self.artist_list.item(row).data(TRACK_ROLE) == chosen:
                    match = row
                    break
            self.artist_list.setCurrentRow(match)
        else:
            self.artist_albums.set_albums([])
            self.artist_title.setText("")
            self.artist_count.setText("")

    def _artist_selected(self, item, _previous=None) -> None:
        if item is None:
            return
        name = item.data(TRACK_ROLE)
        self.artist_title.setText(name)
        albums = self.library.albums(album_artist=name, sort="year")
        self.artist_albums.set_albums(albums)
        songs = sum(int(album["tracks"] or 0) for album in albums)
        self.artist_count.setText(
            f"{_count(len(albums), 'album')}  ·  {_count(songs, 'song')}")

    def _play_artist(self) -> None:
        name = self.artist_title.text()
        if not name:
            return
        self.player.set_queue(self.library.tracks(album_artist=name, sort="album"), 0)

    def _reload_playlists(self) -> None:
        current = self.playlist_list.currentItem()
        chosen = current.data(TRACK_ROLE) if current else None
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()
        for playlist in self.library.playlists():
            item = QListWidgetItem(f"{playlist['name']}\n{playlist['tracks']} songs")
            item.setData(TRACK_ROLE, playlist["id"])
            self.playlist_list.addItem(item)
        self.playlist_list.blockSignals(False)
        if self.playlist_list.count():
            match = 0
            for row in range(self.playlist_list.count()):
                if self.playlist_list.item(row).data(TRACK_ROLE) == chosen:
                    match = row
                    break
            self.playlist_list.setCurrentRow(match)
        else:
            self.playlist_tracks.set_tracks([])
            self.playlist_title.setText("No playlists yet")

    def _playlist_selected(self, item, _previous=None) -> None:
        if item is None:
            return
        playlist_id = int(item.data(TRACK_ROLE))
        name = item.text().strip().splitlines()[0].strip()
        self.playlist_title.setText(name)
        self.playlist_tracks.set_tracks(self.library.tracks(playlist_id=playlist_id))
        self._current_playlist = playlist_id

    # ---------------------------------------------------------------- actions

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a music folder", str(Path.home()))
        if folder:
            self.folderAdded.emit(folder)

    def shuffle_albums(self) -> None:
        self.album_stack.setCurrentIndex(0)
        albums = self.grid.selected_albums() or self.grid.albums
        count = self.player.play_albums(albums, shuffle_albums=True)
        if count:
            self.toast.flash(f"Queued {count} albums in a random order")
            self.now_playing.shuffle.setChecked(self.player.shuffle)

    def _open_album_here(self, album: dict) -> None:
        self.set_tab("albums")
        self.open_album(album)

    def open_album(self, album: dict) -> None:
        tracks = self.library.tracks(album=album["album"],
                                     album_artist=album["album_artist"], sort="album")
        self.detail_title.setText(f"{album['album']}  ·  {album['album_artist']}")
        self.detail_tracks.set_tracks(tracks)
        self.album_stack.setCurrentIndex(1)

    # ------------------------------------------- going to where a song lives

    def show_album(self, album: str, album_artist: str) -> None:
        """Open the album a song came from, wherever you were."""
        match = next((row for row in self.library.albums()
                      if row["album"] == album
                      and row["album_artist"] == album_artist), None)
        if match is None:                  # tagged with an artist, not an album artist
            match = next((row for row in self.library.albums()
                          if row["album"] == album), None)
        if match is None:
            self.toast.flash(f"{album} is not on the shelf")
            return
        self.set_tab("albums")
        self.open_album(match)

    def show_artist(self, name: str) -> None:
        """Open an artist's page, with everything of theirs listed."""
        self.search.clear()
        self.set_tab("artists")
        for row in range(self.artist_list.count()):
            item = self.artist_list.item(row)
            if item.data(TRACK_ROLE) == name:
                self.artist_list.setCurrentItem(item)
                self.artist_list.scrollToItem(item)
                return
        self.toast.flash(f"{name} is not on the shelf")

    def _album_menu(self, albums: list, where: QPoint) -> None:
        menu = QMenu(self)
        play = menu.addAction("Play")
        play.triggered.connect(lambda: self.player.play_albums(albums))
        shuffle = menu.addAction("Shuffle these albums")
        shuffle.triggered.connect(lambda: self.player.play_albums(albums, shuffle_albums=True))
        queue = menu.addAction("Add to the queue")
        queue.triggered.connect(lambda: self._enqueue(self._album_tracks(albums), False))
        menu.addSeparator()
        playlist = menu.addAction("Add to a playlist…")
        playlist.triggered.connect(lambda: self.add_to_playlist(self._album_tracks(albums)))
        convert = menu.addAction("Convert to another format…")
        convert.triggered.connect(lambda: self.convertRequested.emit(self._album_tracks(albums)))
        menu.exec(where)

    def _album_tracks(self, albums: list) -> list:
        tracks = []
        for album in albums:
            tracks.extend(self.library.tracks(album=album["album"],
                                              album_artist=album["album_artist"], sort="album"))
        return tracks

    def _enqueue(self, tracks: list, next_up: bool) -> None:
        added = self.player.enqueue(tracks, next_up)
        if added:
            self.toast.flash(f"Queued {added} song{'s' if added != 1 else ''}")

    def _like(self, track: dict) -> None:
        self.player.toggle_liked(track)

    def _liked_changed(self, track_id: int, _liked: bool) -> None:
        for table in (self.songs, self.detail_tracks, self.playlist_tracks):
            table.refresh_like(track_id)
        current = self.player.current
        if current and current["id"] == track_id:
            self.now_playing._refresh_like(current)

    def new_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "New playlist", "Name:")
        if ok and name.strip():
            self.library.create_playlist(name.strip())
            self._reload_playlists()

    def add_to_playlist(self, tracks: list) -> None:
        if not tracks:
            return
        playlists = self.library.playlists()
        names = [p["name"] for p in playlists] + ["New playlist…"]
        choice, ok = QInputDialog.getItem(self, "Add to a playlist", "Playlist:", names, 0, False)
        if not ok:
            return
        if choice == "New playlist…":
            name, ok = QInputDialog.getText(self, "New playlist", "Name:")
            if not ok or not name.strip():
                return
            playlist_id = self.library.create_playlist(name.strip())
        else:
            playlist_id = next(p["id"] for p in playlists if p["name"] == choice)
        self.library.add_to_playlist(playlist_id, [t["id"] for t in tracks])
        self.toast.flash(f"Added {len(tracks)} song{'s' if len(tracks) != 1 else ''}")
        if self.tab == "playlists":
            self._reload_playlists()

    def _remove_from_playlist(self, tracks: list) -> None:
        playlist_id = getattr(self, "_current_playlist", None)
        if playlist_id is None:
            return
        self.library.remove_from_playlist(playlist_id, [t["id"] for t in tracks])
        self._playlist_selected(self.playlist_list.currentItem())
        self._reload_playlists()

    def _playlist_menu(self, where: QPoint) -> None:
        item = self.playlist_list.itemAt(where)
        if item is None:
            return
        playlist_id = int(item.data(TRACK_ROLE))
        menu = QMenu(self)
        play = menu.addAction("Play")
        play.triggered.connect(
            lambda: self.player.set_queue(self.library.tracks(playlist_id=playlist_id), 0))
        rename = menu.addAction("Rename…")
        rename.triggered.connect(lambda: self._rename_playlist(playlist_id))
        menu.addSeparator()
        delete = menu.addAction("Delete this playlist")
        delete.triggered.connect(lambda: (self.library.delete_playlist(playlist_id),
                                          self._reload_playlists()))
        menu.exec(self.playlist_list.viewport().mapToGlobal(where))

    def _rename_playlist(self, playlist_id: int) -> None:
        name, ok = QInputDialog.getText(self, "Rename playlist", "Name:")
        if ok and name.strip():
            self.library.rename_playlist(playlist_id, name.strip())
            self._reload_playlists()

    def toggle_queue(self) -> None:
        button = self.now_playing.queue_button
        button.setChecked(not button.isChecked())
        self._toggle_queue()

    def _toggle_queue(self) -> None:
        self._show_pane(self.queue_panel, self.now_playing.queue_button.isChecked())

    def toggle_lyrics(self) -> None:
        self.now_playing.lyrics_button.setChecked(
            not self.now_playing.lyrics_button.isChecked())
        self._toggle_lyrics()

    def _toggle_lyrics(self) -> None:
        showing = self.now_playing.lyrics_button.isChecked()
        self._show_pane(self.lyrics_panel, showing)
        if showing and self.player.current:
            self.lyrics_panel.load_for(self.player.current)

    def reload_lyrics(self) -> None:
        """Read the lyrics again — they have changed on disk."""
        self.lyrics_panel.forget()
        if self.lyrics_panel.isVisible() and self.player.current:
            self.lyrics_panel.load_for(self.player.current)

    @staticmethod
    def unless_typing(action) -> None:
        """Do it, unless the keystroke was meant for a text field."""
        if not typing_now():
            action()

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()


class _LyricsJob(QRunnable):
    """Reading lyrics can mean running ffprobe, so it happens off the GUI thread."""

    def __init__(self, track_id: int, path: str, signals):
        super().__init__()
        self.track_id = track_id
        self.path = path
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        found = find_lyrics(self.path)
        self.signals.loaded.emit(self.track_id, with_rests(found.lines),
                                 found.synced, found.source)


class _LyricsSignals(QObject):
    # `object` rather than `list` because a line is more than the pair it
    # looks like — it carries its word timings — and object says plainly
    # that it travels as itself.
    loaded = Signal(int, object, bool, str)


class LyricsPanel(QWidget):
    """The words, following along when the file has timings."""

    def __init__(self, player, library, parent=None):
        super().__init__(parent)
        self.player = player
        self.library = library
        self.setObjectName("Sidebar")
        self.setMinimumWidth(PANE_MIN)
        self._lines: list = []
        self._synced = False
        self._worded = False
        self._current = -1
        self._track_id = -1
        self._cache: dict[int, tuple] = {}

        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("Lyrics")
        title.setObjectName("SectionLabel")
        header.addWidget(title)
        header.addStretch(1)
        self.origin = QLabel("")
        self.origin.setObjectName("HintLabel")
        header.addWidget(self.origin)
        column.addLayout(header)

        self.list = QListWidget()
        self.list.setObjectName("LyricsList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemClicked.connect(self._seek_to)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)   # re-measure on every width
        self.painter = SungLineDelegate(LINE_ROLE, self.list, padding=8)
        self.painter.ink = QColor(style.TEXT)
        self.painter.glow = QColor(style.ACCENT)
        self.painter.glow.setAlpha(46)
        self.list.setItemDelegate(self.painter)
        column.addWidget(self.list, 1)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _LyricsSignals(self)
        self._signals.loaded.connect(self._show)

        player.trackChanged.connect(self.load_for)
        player.positionChanged.connect(self._follow)

    @property
    def word_sync(self) -> bool:
        return bool(self.library.get("word_sync", True))

    @property
    def nudge(self) -> float:
        return lyric_nudge(self.library)

    def set_word_sync(self, on: bool) -> None:
        self.library.set("word_sync", bool(on))
        self.painter.enabled = self._worded and on
        self.list.viewport().update()

    def forget(self) -> None:
        """Drop what has been read, so the next look goes back to the files."""
        self._cache.clear()

    def load_for(self, track: dict) -> None:
        if not track:
            self._track_id = -1
            self._set_lines([], False, "")
            return
        self._track_id = track["id"]
        cached = self._cache.get(track["id"])
        if cached is not None:
            self._set_lines(*cached)
            return
        self.list.clear()
        self.list.addItem(QListWidgetItem("  Looking for lyrics…"))
        self.origin.setText("")
        self._pool.start(_LyricsJob(track["id"], track["path"], self._signals))

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(1500)

    def _show(self, track_id: int, lines: list, synced: bool, source: str) -> None:
        self._cache[track_id] = (lines, synced, source)
        if track_id == self._track_id:
            self._set_lines(lines, synced, source)

    def _set_lines(self, lines: list, synced: bool, source: str) -> None:
        self._lines = list(lines)
        self._synced = synced
        self._current = -1
        self.painter.start_over(synced)
        self.list.clear()
        if not lines:
            placeholder = QListWidgetItem(
                "  No lyrics for this song.\n\n  Aerwave looks for an .lrc file beside the\n"
                "  track, then for lyrics saved inside it.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
            self.origin.setText("")
            return
        self.origin.setText({"file": ".lrc file", "tag": "in the file"}.get(source, ""))
        self._worded = any(getattr(line, "words", ()) for line in lines)
        self.painter.enabled = self._worded and self.word_sync
        for line in lines:
            item = QListWidgetItem("")
            item.setData(TRACK_ROLE, line[0])
            item.setData(LINE_ROLE, line)
            self.list.addItem(item)
        self.list.doItemsLayout()      # the rows are as tall as the text needs
        note = "word by word" if self.painter.enabled else ("" if synced else "not timed")
        if note:
            self.origin.setText((self.origin.text() + " · " + note).strip(" ·"))

    def _follow(self, position: float, _duration: float) -> None:
        if not self._synced or not self._lines or not self.isVisible():
            return
        position += self.nudge
        index = -1
        for position_index, (start, _text) in enumerate(self._lines):
            if start is not None and start <= position + 0.05:
                index = position_index
            else:
                break
        moved = index != self._current
        self._current = index
        self.painter.follow(index, position)
        if moved:
            for row in range(self.list.count()):
                item = self.list.item(row)
                font = QFont(item.font())
                font.setWeight(QFont.Weight.DemiBold if row == index
                               else QFont.Weight.Normal)
                item.setFont(font)
            if 0 <= index < self.list.count():
                self.list.scrollToItem(self.list.item(index),
                                       QAbstractItemView.ScrollHint.PositionAtCenter)
        # Repaint every tick, not only when the line changes: within a line
        # it is the words that are moving.
        if self._worded:
            self.list.viewport().update()
        elif moved:
            self.list.viewport().update()

    def _seek_to(self, item: QListWidgetItem) -> None:
        start = item.data(TRACK_ROLE)
        if start is not None and self._synced:
            self.player.seek(float(start))
