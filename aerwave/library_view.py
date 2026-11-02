"""The library page: folder sidebar, search/sort bar and the poster grid."""

import subprocess
import time
from pathlib import Path

from PySide6.QtCore import (QEvent, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                           QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu,
                               QPushButton, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

from . import motion, style
from .icons import icon
from .widgets import (ElidedLabel, FillingGrid, draw_art_ring, draw_play_disc, format_duration,
                      format_time, glass_menu)

VIDEO_ROLE = Qt.ItemDataRole.UserRole + 1

CARD_LEAST = 280              # a poster is never narrower than this
CARD_TEXT = 52                # the two lines under it
GRID_SPACING = 22
GUTTER = 24                   # the page's margin, and the grid lands on it
GRID_EDGE = 8        # air between a grid and the bars above and below

SCOPES = [
    ("all", "All videos", "grid"),
    ("continue", "Continue watching", "clock"),
    ("unwatched", "Not started", "eye"),
    ("watched", "Finished", "check"),
]

SORT_OPTIONS = [
    ("added", "Recently added"),
    ("episode", "Series & episode"),
    ("recent", "Recently played"),
    ("title", "Title (A–Z)"),
    ("duration", "Longest first"),
    ("size", "Largest first"),
    ("folder", "Folder path"),
]


def scaled_font(base: QFont, delta_points: float, weight: QFont.Weight | None = None) -> QFont:
    """Resize a font by `delta_points`, honouring pixel-sized fonts from the stylesheet."""
    font = QFont(base)
    if base.pointSizeF() > 0:
        font.setPointSizeF(max(6.0, base.pointSizeF() + delta_points))
    else:
        font.setPixelSize(max(9, int(round(base.pixelSize() + delta_points * 1.34))))
    if weight is not None:
        font.setWeight(weight)
    return font


def relative_time(timestamp: float) -> str:
    if not timestamp:
        return ""
    delta = max(0, time.time() - timestamp)
    if delta < 90:
        return "just now"
    for size, label in ((3600, "min"), (86400, "hour"), (604800, "day")):
        if delta < size * (60 if label == "min" else 24 if label == "hour" else 7):
            value = int(delta // (60 if label == "min" else 3600 if label == "hour" else 86400))
            return f"{value} {label}{'s' if value != 1 else ''} ago"
    return time.strftime("%d %b %Y", time.localtime(timestamp))


def _card_height(width: int) -> int:
    return width * 9 // 16 + CARD_TEXT


class VideoDelegate(QStyledItemDelegate):
    """Draws one poster card: thumbnail, progress, duration and titles."""

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.card = QSize(CARD_LEAST, _card_height(CARD_LEAST))

    def sizeHint(self, _option, _index) -> QSize:
        return self.card

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        video = index.data(VIDEO_ROLE)
        if not video:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # The picture is the card: nothing is drawn around it.
        thumb_rect = QRectF(rect.left(), rect.top(), rect.width(),
                            option.rect.height() - CARD_TEXT)
        clip = QPainterPath()
        clip.addRoundedRect(thumb_rect, style.ART_RADIUS, style.ART_RADIUS)
        painter.save()
        painter.setClipPath(clip)
        pixmap = self.thumbs.get(video)
        if pixmap is None:
            pixmap = self.thumbs.placeholder(int(thumb_rect.width()), int(thumb_rect.height()))
        scaled = pixmap.scaled(
            thumb_rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        offset_x = thumb_rect.left() + (thumb_rect.width() - scaled.width()) / 2
        offset_y = thumb_rect.top() + (thumb_rect.height() - scaled.height()) / 2
        painter.drawPixmap(QPoint(int(offset_x), int(offset_y)), scaled)

        if hovered:
            painter.fillRect(thumb_rect, QColor(0, 0, 0, 80))
            draw_play_disc(painter, thumb_rect.center(), 22)
        painter.restore()
        if selected:
            draw_art_ring(painter, thumb_rect,
                          bool(option.state & QStyle.StateFlag.State_HasFocus))

        # Duration pill.
        duration = video.get("duration") or 0
        if duration:
            text = format_time(duration, duration >= 3600)
            metrics = QFontMetrics(painter.font())
            width = metrics.horizontalAdvance(text) + 14
            pill = QRectF(thumb_rect.right() - width - 8, thumb_rect.bottom() - 28, width, 20)
            painter.setBrush(QColor(8, 9, 12, 205))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill, 3, 3)
            painter.setPen(QColor("#f2f4f8"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

        # Watched tick.
        if video.get("watched"):
            badge = QRectF(thumb_rect.left() + 8, thumb_rect.top() + 8, 24, 24)
            painter.setBrush(QColor(style.GOOD))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badge, 3, 3)
            painter.drawPixmap(
                QPoint(int(badge.left() + 3), int(badge.top() + 3)),
                icon("check", 18, style.BG).pixmap(18, 18),
            )

        # Resume progress.
        position = video.get("position") or 0
        if duration and position > 5 and not video.get("watched"):
            ratio = min(1.0, position / duration)
            track = QRectF(thumb_rect.left(), thumb_rect.bottom() - 4, thumb_rect.width(), 4)
            painter.fillRect(track, QColor(255, 255, 255, 45))
            painter.fillRect(QRectF(track.left(), track.top(), track.width() * ratio, 4),
                             QColor(style.ACCENT))

        # Titles.
        text_left = int(rect.left())
        text_width = int(rect.width())
        title_font = scaled_font(painter.font(), 0.5, QFont.Weight.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(style.TEXT))
        metrics = QFontMetrics(title_font)
        title = metrics.elidedText(video["title"], Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRect(text_left, int(thumb_rect.bottom()) + 10, text_width, 20),
                         Qt.AlignmentFlag.AlignVCenter, title)

        detail_font = scaled_font(title_font, -0.8, QFont.Weight.Normal)
        painter.setFont(detail_font)
        painter.setPen(QColor(style.MUTED))
        if video.get("series"):
            bits = [f"S{video['season']:02d}E{video['episode']:02d}", video["series"]]
        else:
            bits = [Path(video["path"]).parent.name]
        if video.get("height"):
            bits.append(f"{video['height']}p")
        if (video.get("play_count") or 0) > 1:
            bits.append(f"watched {video['play_count']}×")
        if position > 5 and duration and not video.get("watched"):
            bits.append(f"{format_time(duration - position)} left")
        elif video.get("last_played"):
            bits.append(relative_time(video["last_played"]))
        detail = QFontMetrics(detail_font).elidedText(
            "  ·  ".join(b for b in bits if b), Qt.TextElideMode.ElideRight, text_width
        )
        painter.drawText(QRect(text_left, int(thumb_rect.bottom()) + 30, text_width, 18),
                         Qt.AlignmentFlag.AlignVCenter, detail)
        painter.restore()


class LibraryPage(QWidget):
    """Browse, search and launch everything in the library."""

    playRequested = Signal(dict, bool)   # video, resume
    folderAdded = Signal(str)
    rescanRequested = Signal()
    rebuildRequested = Signal()
    glassChanged = Signal(str)          # solid | frosted | clear
    headingChanged = Signal(str)
    helpRequested = Signal()
    transcribeRequested = Signal(dict)
    cancelTranscribeRequested = Signal(int)

    def __init__(self, library, thumbs, parent=None):
        super().__init__(parent)
        self.library = library
        self.thumbs = thumbs
        self.scope = "all"
        self.root: str | None = None
        self.series: str | None = None
        self.videos: list[dict] = []
        self._row_by_id: dict[int, int] = {}
        self._transcribing: dict[int, float] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # The window hangs these two on its own frame, so that all three
        # libraries share one sidebar and one header.
        self.heading = "Videos"
        self.sidebar = self._build_sidebar()
        self.toolbar = self._build_top_bar()

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_grid(), 1)
        right.addWidget(self._build_status_bar())
        container = QWidget()
        container.setLayout(right)
        root.addWidget(container, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self.reload)

        thumbs.ready.connect(self._on_thumb_ready)

    # ------------------------------------------------------------------ build

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        label = QLabel("Library")
        label.setObjectName("SectionLabel")
        column.addWidget(label)

        self.scope_buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in SCOPES:
            button = QPushButton(f"  {text}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.setIcon(icon(icon_name, 17, style.MUTED))
            button.setIconSize(QSize(17, 17))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.set_scope(k))
            column.addWidget(button)
            self.scope_buttons[key] = button

        column.addSpacing(14)
        folders_label = QLabel("Folders")
        folders_label.setObjectName("SectionLabel")
        column.addWidget(folders_label)

        self.folder_box = QVBoxLayout()
        self.folder_box.setSpacing(2)
        column.addLayout(self.folder_box)

        self.series_label = QLabel("Shows")
        self.series_label.setObjectName("SectionLabel")
        column.addSpacing(14)
        column.addWidget(self.series_label)
        self.series_box = QVBoxLayout()
        self.series_box.setSpacing(2)
        column.addLayout(self.series_box)
        column.addStretch(1)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("HintLabel")
        self.stats_label.setWordWrap(True)
        self.stats_label.setContentsMargins(10, 0, 10, 0)
        column.addWidget(self.stats_label)
        column.addSpacing(6)

        add_button = QPushButton("  Add folder")
        add_button.setObjectName("Primary")
        add_button.setIcon(icon("folder_add", 18, style.ON_ACCENT))
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.clicked.connect(self.choose_folder)
        column.addWidget(add_button)
        return panel

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)

        self.search_box = QLineEdit()
        # Click to search, but do not take the keyboard the moment the
        # app opens: a single-letter shortcut belongs to the music.
        self.search_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_box.setPlaceholderText("Search your library…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.addAction(icon("search", 16, style.MUTED),
                                  QLineEdit.ActionPosition.LeadingPosition)
        self.search_box.setFixedWidth(280)
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search_box)

        self.sort_box = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_box.addItem(label, key)
        saved_sort = self.library.get("sort", "added")
        index = self.sort_box.findData(saved_sort)
        self.sort_box.setCurrentIndex(max(0, index))
        self.sort_box.currentIndexChanged.connect(self._on_sort_changed)
        self.sort_box.setFixedWidth(172)
        row.addWidget(self.sort_box)

        rescan = QPushButton()
        rescan.setObjectName("Flat")
        rescan.setIcon(icon("refresh", 17, style.TEXT))
        rescan.setIconSize(QSize(17, 17))
        rescan.setFixedSize(33, 33)
        rescan.setToolTip("Rescan folders")
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.clicked.connect(self.rescanRequested)
        motion.press_dip(rescan)
        self.rescan_button = rescan
        row.addWidget(rescan)

        overflow = QPushButton()
        overflow.setObjectName("Flat")
        overflow.setIcon(icon("more", 18, style.TEXT))
        overflow.setIconSize(QSize(18, 18))
        overflow.setToolTip("More")
        overflow.setFixedSize(34, 34)
        overflow.setCursor(Qt.CursorShape.PointingHandCursor)
        overflow.clicked.connect(lambda: self._open_overflow_menu(overflow))
        row.addWidget(overflow)
        return bar

    def _open_overflow_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        add = menu.addAction("Add folder…")
        add.triggered.connect(self.choose_folder)
        rescan = menu.addAction("Rescan folders")
        rescan.triggered.connect(self.rescanRequested)
        forget = menu.addAction("Forget files that are gone")
        forget.triggered.connect(self._purge_missing)
        menu.addSeparator()
        rebuild = menu.addAction("Rebuild library…")
        rebuild.triggered.connect(self.rebuildRequested)
        glass_menu(menu, self.library, self.glassChanged.emit)
        menu.addSeparator()
        shortcuts = menu.addAction("Keyboard shortcuts")
        shortcuts.triggered.connect(self.helpRequested)
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _purge_missing(self) -> None:
        removed = self.library.purge_missing()
        self.reload()
        self.status_label.setText(
            f"Removed {removed} missing file{'s' if removed != 1 else ''}"
            if removed else "Nothing missing — every file is still on disk"
        )

    def _build_grid(self) -> QWidget:
        self.model = QStandardItemModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        delegate = VideoDelegate(self.thumbs, self)
        self.view.setItemDelegate(delegate)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setFlow(QListView.Flow.LeftToRight)
        self.view.setWrapping(True)
        self.view.setUniformItemSizes(True)
        self._fill = FillingGrid(self.view, delegate, CARD_LEAST, GRID_SPACING, _card_height)
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(28)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.doubleClicked.connect(lambda idx: self._play(idx, resume=True))
        self.view.customContextMenuRequested.connect(self._open_context_menu)
        self.view.installEventFilter(self)

        self.empty_state = self._build_empty_state()

        wrapper = QWidget()
        stack = QVBoxLayout(wrapper)
        # The grid keeps half a gap down each side of every cell, which is
        # taken off the margin so its outer edges land on the page's.
        side = GUTTER - GRID_SPACING // 2
        stack.setContentsMargins(side, GRID_EDGE, 0, 0)  # the scroll bar has the rest
        stack.addWidget(self.view)
        stack.addWidget(self.empty_state)
        self.empty_state.hide()
        return wrapper

    def _build_empty_state(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.setSpacing(10)
        art = QLabel()
        art.setPixmap(icon("logo", 68, style.BORDER).pixmap(68, 68))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(art)
        self.empty_title = QLabel("Your library is empty")
        self.empty_title.setObjectName("EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.empty_title)
        self.empty_body = QLabel("Point Aerwave at a folder of videos and it will index everything inside.")
        self.empty_body.setObjectName("EmptyBody")
        self.empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.empty_body)
        button = QPushButton("  Choose a folder")
        button.setObjectName("Primary")
        button.setIcon(icon("folder_add", 18, style.ON_ACCENT))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMaximumWidth(200)
        button.clicked.connect(self.choose_folder)
        column.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        return panel

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(GUTTER, 6, GUTTER, 8)
        self.status_label = ElidedLabel("")
        self.status_label.setObjectName("StatusLabel")
        row.addWidget(self.status_label)
        return bar

    # ------------------------------------------------------------------- data

    def reload(self) -> None:
        selected_id = None
        current = self.view.currentIndex()
        if current.isValid():
            video = current.data(VIDEO_ROLE)
            selected_id = video["id"] if video else None

        sort = self.sort_box.currentData() or "added"
        if self.series is not None:
            sort = "episode"     # inside a show, episode order is the only sane order
        self.videos = self.library.videos(
            search=self.search_box.text().strip(),
            scope=self.scope,
            root=self.root,
            series=self.series,
            sort=sort,
        )
        self.model.clear()
        self._row_by_id.clear()
        for row, video in enumerate(self.videos):
            item = QStandardItem()
            item.setData(video, VIDEO_ROLE)
            item.setEditable(False)
            self.model.appendRow(item)
            self._row_by_id[video["id"]] = row

        self._refresh_folders()
        self._refresh_series()
        self._refresh_stats()
        has_folders = bool(self.library.folders())
        empty = not self.videos
        self.empty_state.setVisible(empty)
        self.view.setVisible(not empty)
        if empty:
            if not has_folders:
                self.empty_title.setText("Your library is empty")
                self.empty_body.setText(
                    "Point Aerwave at a folder of videos and it will index everything inside."
                )
            elif self.search_box.text().strip():
                self.empty_title.setText("Nothing matches that search")
                self.empty_body.setText("Try a different word, or clear the search box.")
            else:
                self.empty_title.setText("Nothing here yet")
                self.empty_body.setText(
                    "This view is empty. Pick another section, or rescan your folders."
                )
        if selected_id is not None and selected_id in self._row_by_id:
            index = self.model.index(self._row_by_id[selected_id], 0)
            self.view.setCurrentIndex(index)

    def set_transcribing(self, video_id: int, fraction: float | None) -> None:
        """Track which cards are having subtitles made, for the context menu."""
        if fraction is None:
            self._transcribing.pop(video_id, None)
        else:
            self._transcribing[video_id] = fraction

    def refresh_video(self, video_id: int) -> None:
        """Re-read one row after its progress changed, without rebuilding the grid."""
        row = self._row_by_id.get(video_id)
        if row is None:
            return
        fresh = self.library.video(video_id)
        if fresh is None:
            return
        item = self.model.item(row)
        if item is not None:
            item.setData(fresh, VIDEO_ROLE)
            self.videos[row] = fresh
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 0))
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        counts = self.library.counts()
        self.stats_label.setText(
            f"{counts['total']} videos · {format_duration(counts['seconds'])}\n"
            f"{counts['watched']} finished · {counts['continuing']} in progress"
        )
        shown = len(self.videos)
        scope_name = dict((k, t) for k, t, _ in SCOPES)[self.scope]
        if self.series:
            where = f" in {self.series}"
        elif self.root:
            where = f" in {Path(self.root).name}"
        else:
            where = ""
        self.status_label.setText(f"{shown} of {counts['total']} · {scope_name}{where}")
        self.heading = self.series or (Path(self.root).name if self.root else scope_name)
        self.headingChanged.emit(self.heading)

    def _refresh_folders(self) -> None:
        while self.folder_box.count():
            item = self.folder_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        folders = self.library.folders()
        all_button = QPushButton("  All folders")
        all_button.setObjectName("NavItem")
        all_button.setCheckable(True)
        all_button.setChecked(self.root is None)
        all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        all_button.clicked.connect(lambda: self.set_root(None))
        self.folder_box.addWidget(all_button)
        for folder in folders:
            button = QPushButton(f"  {Path(folder).name or folder}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setChecked(self.root == folder)
            button.setToolTip(folder)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, f=folder: self.set_root(f))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, f=folder, b=button: self._folder_menu(f, b)
            )
            self.folder_box.addWidget(button)

    def _refresh_series(self) -> None:
        while self.series_box.count():
            item = self.series_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        shows = self.library.series()
        visible = bool(shows)
        self.series_label.setVisible(visible)
        if not visible:
            if self.series is not None:
                self.series = None
            return
        for show in shows:
            left = show["episodes"] - show["watched"]
            button = QPushButton(f"  {show['name']}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setChecked(self.series == show["name"])
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(
                f"{show['episodes']} episodes · "
                f"{left} still to watch" if left else f"{show['episodes']} episodes · all watched")
            button.clicked.connect(lambda _c=False, n=show["name"]: self.set_series(n))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, n=show["name"], b=button: self._series_menu(n, b))
            self.series_box.addWidget(button)

    def _series_menu(self, name: str, anchor: QWidget) -> None:
        menu = QMenu(self)
        following = self.library.next_unwatched(name)
        if following:
            label = f"Play next episode — S{following['season']:02d}E{following['episode']:02d}"
            action = menu.addAction(label)
            action.triggered.connect(lambda: self.playRequested.emit(following, True))
        else:
            done = menu.addAction("Every episode watched")
            done.setEnabled(False)
        menu.addSeparator()
        show_all = menu.addAction("Show this series")
        show_all.triggered.connect(lambda: self.set_series(name))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def play_next_episode(self) -> bool:
        """Play the earliest unwatched episode of the selected show."""
        if self.series is None:
            return False
        following = self.library.next_unwatched(self.series)
        if following is None:
            self.status_label.setText(f"{self.series}: every episode is watched")
            return False
        self.playRequested.emit(following, True)
        return True

    # ---------------------------------------------------------------- actions

    def set_series(self, series: str | None) -> None:
        self.series = None if series == self.series else series
        if self.series is not None:
            self.root = None          # a show and a folder are separate ways to narrow
        self.reload()

    def set_scope(self, scope: str) -> None:
        self.scope = scope
        for key, button in self.scope_buttons.items():
            button.setChecked(key == scope)
        self.reload()

    def set_root(self, root: str | None) -> None:
        self.root = root
        if root is not None:
            self.series = None
        self.reload()

    def _on_sort_changed(self) -> None:
        self.library.set("sort", self.sort_box.currentData())
        self.reload()

    def choose_folder(self) -> None:
        start = str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Add a folder to your library", start)
        if folder:
            self.folderAdded.emit(folder)

    def _folder_menu(self, folder: str, anchor: QWidget) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open in file manager")
        open_action.triggered.connect(lambda: subprocess.Popen(["xdg-open", folder]))
        menu.addSeparator()
        remove = menu.addAction("Remove folder from library")
        remove.triggered.connect(lambda: self._remove_folder(folder))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _remove_folder(self, folder: str) -> None:
        self.library.remove_folder(folder)
        if self.root == folder:
            self.root = None
        self.reload()

    def _play(self, index: QModelIndex, resume: bool = True) -> None:
        video = index.data(VIDEO_ROLE)
        if video:
            self.playRequested.emit(video, resume)

    def play_current(self, resume: bool = True) -> None:
        index = self.view.currentIndex()
        if index.isValid():
            self._play(index, resume)
        elif self.videos:
            self.view.setCurrentIndex(self.model.index(0, 0))
            self._play(self.model.index(0, 0), resume)

    def neighbour(self, video_id: int, direction: int) -> dict | None:
        row = self._row_by_id.get(video_id)
        if row is None:
            return None
        target = row + direction
        if 0 <= target < len(self.videos):
            return self.videos[target]
        return None

    def focus_search(self) -> None:
        self.search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_box.selectAll()

    def _open_context_menu(self, position: QPoint) -> None:
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        video = index.data(VIDEO_ROLE)
        menu = QMenu(self)

        resume_at = video.get("position") or 0
        if resume_at > 5 and not video.get("watched"):
            resume = menu.addAction(f"Resume at {format_time(resume_at)}")
            resume.triggered.connect(lambda: self.playRequested.emit(video, True))
        play = menu.addAction("Play from the beginning")
        play.triggered.connect(lambda: self.playRequested.emit(video, False))
        menu.addSeparator()

        watched = bool(video.get("watched"))
        toggle = menu.addAction("Mark as unwatched" if watched else "Mark as watched")
        toggle.triggered.connect(lambda: self._set_watched(video, not watched))
        if resume_at > 5:
            clear = menu.addAction("Forget my position")
            clear.triggered.connect(lambda: self._reset(video))
        menu.addSeparator()

        from . import subtitles as subtitle_tools
        existing = subtitle_tools.existing_subtitle(video["path"])
        if self._transcribing.get(video["id"]) is not None:
            stop = menu.addAction("Stop generating subtitles")
            stop.triggered.connect(
                lambda: self.cancelTranscribeRequested.emit(video["id"]))
        elif existing:
            again = menu.addAction("Generate subtitles again")
            again.triggered.connect(lambda: self.transcribeRequested.emit(video))
        else:
            make = menu.addAction("Generate subtitles from the audio…")
            make.triggered.connect(lambda: self.transcribeRequested.emit(video))
        menu.addSeparator()

        thumb = menu.addAction("Regenerate thumbnail")
        thumb.triggered.connect(lambda: self._regen_thumb(video))
        reveal = menu.addAction("Show in file manager")
        reveal.triggered.connect(
            lambda: subprocess.Popen(["xdg-open", str(Path(video["path"]).parent)])
        )
        copy = menu.addAction("Copy file path")
        copy.triggered.connect(lambda: QApplication.clipboard().setText(video["path"]))
        menu.addSeparator()
        remove = menu.addAction("Remove from library")
        remove.triggered.connect(lambda: self._remove(video))
        menu.exec(self.view.viewport().mapToGlobal(position))

    def _set_watched(self, video: dict, watched: bool) -> None:
        self.library.set_watched(video["id"], watched, reset_position=watched)
        self.refresh_video(video["id"])
        if self.scope != "all":
            self.reload()

    def _reset(self, video: dict) -> None:
        self.library.reset_progress(video["id"])
        self.refresh_video(video["id"])
        if self.scope != "all":
            self.reload()

    def _regen_thumb(self, video: dict) -> None:
        self.thumbs.forget(video)
        self.thumbs.request(video)

    def _remove(self, video: dict) -> None:
        self.library.remove_video(video["id"])
        self.reload()

    def _on_thumb_ready(self, video_id: int) -> None:
        row = self._row_by_id.get(video_id)
        if row is not None:
            index = self.model.index(row, 0)
            self.model.dataChanged.emit(index, index)

    # ----------------------------------------------------------------- events

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.play_current()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
