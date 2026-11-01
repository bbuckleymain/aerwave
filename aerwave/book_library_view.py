"""The audiobook shelf: folder sidebar, search/sort bar and a grid of covers."""

import subprocess
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                           QStandardItem, QStandardItemModel)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu,
                               QPushButton, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import style
from .book_player import spoken_length
from .icons import icon
from .library_view import scaled_font
from .widgets import ElidedLabel, format_time, glass_menu

BOOK_ROLE = Qt.ItemDataRole.UserRole + 1

COVER_SIZE = 178
CARD_WIDTH = COVER_SIZE + 24
CARD_HEIGHT = COVER_SIZE + 82
GRID_SPACING = 18
GRID_EDGE = 8        # air between a grid and the bars above and below

SCOPES = [
    ("all", "All books", "book"),
    ("listening", "Listening now", "headphones"),
    ("unstarted", "Not started", "eye"),
    ("finished", "Finished", "check"),
]

SORT_OPTIONS = [
    ("added", "Recently added"),
    ("recent", "Recently played"),
    ("title", "Title (A–Z)"),
    ("author", "Author"),
    ("duration", "Longest first"),
    ("folder", "Folder path"),
]


class BookDelegate(QStyledItemDelegate):
    """One shelf card: cover, title, author and how far in you are."""

    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.covers = covers

    def sizeHint(self, _option, _index) -> QSize:
        return QSize(CARD_WIDTH, CARD_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        book = index.data(BOOK_ROLE)
        if not book:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        if hovered or selected:
            card = QPainterPath()
            card.addRoundedRect(rect, 12, 12)
            painter.fillPath(card, style.with_alpha(style.ELEVATED, 0.92))
            if selected:
                painter.setPen(QColor(style.ACCENT))
                painter.drawPath(card)

        cover_rect = QRectF(rect.left() + 12, rect.top() + 12, COVER_SIZE, COVER_SIZE)
        clip = QPainterPath()
        clip.addRoundedRect(cover_rect, 8, 8)
        painter.save()
        painter.setClipPath(clip)
        pixmap = self.covers.get(book, online=True)
        scaled = pixmap.scaled(cover_rect.size().toSize(),
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(
            QPoint(int(cover_rect.left() + (cover_rect.width() - scaled.width()) / 2),
                   int(cover_rect.top() + (cover_rect.height() - scaled.height()) / 2)),
            scaled)
        if hovered:
            painter.fillRect(cover_rect, QColor(0, 0, 0, 95))
            centre = cover_rect.center()
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(centre, 22, 22)
            play = QPainterPath()
            play.moveTo(centre.x() - 6, centre.y() - 9)
            play.lineTo(centre.x() + 10, centre.y())
            play.lineTo(centre.x() - 6, centre.y() + 9)
            play.closeSubpath()
            painter.setBrush(QColor("#101218"))
            painter.drawPath(play)

        duration = book.get("duration") or 0
        elapsed = book.get("elapsed") or 0
        if duration and elapsed > 30 and not book.get("finished"):
            track = QRectF(cover_rect.left(), cover_rect.bottom() - 4, cover_rect.width(), 4)
            painter.fillRect(track, QColor(0, 0, 0, 130))
            painter.fillRect(QRectF(track.left(), track.top(),
                                    track.width() * min(1.0, elapsed / duration), 4),
                             QColor(style.ACCENT))
        painter.restore()

        if book.get("finished"):
            badge = QRectF(cover_rect.left() + 7, cover_rect.top() + 7, 24, 24)
            painter.setBrush(QColor(style.GOOD))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badge, 7, 7)
            painter.drawPixmap(QPoint(int(badge.left() + 3), int(badge.top() + 3)),
                               icon("check", 18, "#0a1a12").pixmap(18, 18))
        if book.get("bookmark_count"):
            painter.drawPixmap(QPoint(int(cover_rect.right() - 22), int(cover_rect.top() + 6)),
                               icon("bookmark", 18, "#ffffff").pixmap(18, 18))

        text_left = int(rect.left()) + 12
        text_width = COVER_SIZE
        title_font = scaled_font(painter.font(), 0.2, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(style.TEXT))
        metrics = QFontMetrics(title_font)
        painter.drawText(QRect(text_left, int(cover_rect.bottom()) + 9, text_width, 18),
                         Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(book["title"], Qt.TextElideMode.ElideRight, text_width))

        detail_font = scaled_font(title_font, -1.4, QFont.Weight.Normal)
        painter.setFont(detail_font)
        detail_metrics = QFontMetrics(detail_font)
        painter.setPen(QColor(style.MUTED))
        author = book.get("author") or "Unknown author"
        painter.drawText(QRect(text_left, int(cover_rect.bottom()) + 28, text_width, 16),
                         Qt.AlignmentFlag.AlignVCenter,
                         detail_metrics.elidedText(author, Qt.TextElideMode.ElideRight, text_width))

        if book.get("finished"):
            status = "Finished"
            if (book.get("play_count") or 0) > 1:
                status += f"  ·  {book['play_count']}× through"
        elif elapsed > 30 and duration:
            status = f"{spoken_length(duration - elapsed)} left"
        else:
            status = spoken_length(duration)
            if (book.get("tracks") or 0) > 1:
                status += f"  ·  {book['tracks']} files"
        painter.setPen(QColor(style.FAINT))
        painter.drawText(QRect(text_left, int(cover_rect.bottom()) + 46, text_width, 16),
                         Qt.AlignmentFlag.AlignVCenter,
                         detail_metrics.elidedText(status, Qt.TextElideMode.ElideRight, text_width))
        painter.restore()


class BookLibraryPage(QWidget):
    """Browse and start audiobooks."""

    playRequested = Signal(dict, bool)
    folderAdded = Signal(str)
    rescanRequested = Signal()
    rebuildRequested = Signal()
    glassChanged = Signal(str)          # solid | frosted | clear
    tidyRequested = Signal()
    helpRequested = Signal()

    def __init__(self, library, covers, parent=None):
        super().__init__(parent)
        self.library = library
        self.covers = covers
        self.scope = "all"
        self.root: str | None = None
        self.books: list[dict] = []
        self._row_by_id: dict[int, int] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_top_bar())
        right.addWidget(self._build_grid(), 1)
        right.addWidget(self._build_status_bar())
        container = QWidget()
        container.setLayout(right)
        root.addWidget(container, 1)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self.reload)
        covers.ready.connect(self._on_cover_ready)

    # ------------------------------------------------------------------ build

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Sidebar")
        panel.setFixedWidth(214)
        column = QVBoxLayout(panel)
        column.setContentsMargins(12, 14, 12, 12)
        column.setSpacing(4)

        label = QLabel("SHELF")
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
        folders_label = QLabel("FOLDERS")
        folders_label.setObjectName("SectionLabel")
        column.addWidget(folders_label)
        self.folder_box = QVBoxLayout()
        self.folder_box.setSpacing(2)
        column.addLayout(self.folder_box)
        column.addStretch(1)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("HintLabel")
        self.stats_label.setWordWrap(True)
        column.addWidget(self.stats_label)
        column.addSpacing(6)

        add_button = QPushButton("  Add folder")
        add_button.setObjectName("Primary")
        add_button.setIcon(icon("folder_add", 18, "#08111d"))
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.clicked.connect(self.choose_folder)
        column.addWidget(add_button)
        return panel

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)

        self.search_box = QLineEdit()
        # Click to search, but do not take the keyboard the moment the
        # app opens: a single-letter shortcut belongs to the music.
        self.search_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_box.setPlaceholderText("Search titles, authors, series…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.addAction(icon("search", 16, style.MUTED),
                                  QLineEdit.ActionPosition.LeadingPosition)
        self.search_box.setMinimumWidth(260)
        self.search_box.setMaximumWidth(460)
        self.search_box.textChanged.connect(lambda _text: self._search_timer.start())
        row.addWidget(self.search_box, 1)
        row.addStretch(2)

        self.sort_box = QComboBox()
        for key, label in SORT_OPTIONS:
            self.sort_box.addItem(label, key)
        index = self.sort_box.findData(self.library.get("book_sort", "added"))
        self.sort_box.setCurrentIndex(max(0, index))
        self.sort_box.setFixedWidth(172)
        self.sort_box.currentIndexChanged.connect(self._on_sort_changed)
        row.addWidget(self.sort_box)

        rescan = QPushButton("  Rescan")
        rescan.setIcon(icon("refresh", 17, style.TEXT))
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setFixedWidth(112)
        rescan.clicked.connect(self.rescanRequested)
        self.rescan_button = rescan
        row.addWidget(rescan)

        overflow = QPushButton()
        overflow.setObjectName("Flat")
        overflow.setIcon(icon("gear", 18, style.TEXT))
        overflow.setIconSize(QSize(18, 18))
        overflow.setFixedSize(34, 34)
        overflow.setCursor(Qt.CursorShape.PointingHandCursor)
        overflow.clicked.connect(lambda: self._open_overflow_menu(overflow))
        row.addWidget(overflow)
        return bar

    def _build_grid(self) -> QWidget:
        self.model = QStandardItemModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(BookDelegate(self.covers, self))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setWrapping(True)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(GRID_SPACING // 2)
        self.view.setGridSize(QSize(CARD_WIDTH + GRID_SPACING, CARD_HEIGHT + GRID_SPACING))
        self.view.setMouseTracking(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(28)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.doubleClicked.connect(lambda idx: self._play(idx, True))
        self.view.customContextMenuRequested.connect(self._open_context_menu)

        self.empty_state = self._build_empty_state()
        wrapper = QWidget()
        stack = QVBoxLayout(wrapper)
        # A little air above the first row and below the last, so the shelf
        # does not sit flush against the bars: the same on all three grids.
        stack.setContentsMargins(0, GRID_EDGE, 0, GRID_EDGE)
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
        art.setPixmap(icon("headphones", 66, style.BORDER).pixmap(66, 66))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(art)
        self.empty_title = QLabel("No audiobooks yet")
        self.empty_title.setObjectName("EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.empty_title)
        self.empty_body = QLabel(
            "Add a folder of audiobooks. An .m4b is read as one book with its own\n"
            "chapters; a folder of MP3s becomes one book with a chapter per file."
        )
        self.empty_body.setObjectName("EmptyBody")
        self.empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.empty_body)
        button = QPushButton("  Choose a folder")
        button.setObjectName("Primary")
        button.setIcon(icon("folder_add", 18, "#08111d"))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMaximumWidth(200)
        button.clicked.connect(self.choose_folder)
        column.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        return panel

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 6, 14, 6)
        self.status_label = ElidedLabel("")
        self.status_label.setObjectName("StatusLabel")
        row.addWidget(self.status_label)
        row.addStretch(1)
        hint = QLabel("Double-click to listen  ·  picks up where you stopped")
        hint.setObjectName("HintLabel")
        row.addWidget(hint)
        return bar

    # ------------------------------------------------------------------- data

    def reload(self) -> None:
        selected_id = None
        current = self.view.currentIndex()
        if current.isValid():
            book = current.data(BOOK_ROLE)
            selected_id = book["id"] if book else None

        self.books = self.library.books(
            search=self.search_box.text().strip(),
            scope=self.scope,
            root=self.root,
            sort=self.sort_box.currentData() or "added",
        )
        self.model.clear()
        self._row_by_id.clear()
        for row, book in enumerate(self.books):
            item = QStandardItem()
            item.setData(book, BOOK_ROLE)
            item.setEditable(False)
            self.model.appendRow(item)
            self._row_by_id[book["id"]] = row

        self._refresh_folders()
        self._refresh_stats()
        empty = not self.books
        self.empty_state.setVisible(empty)
        self.view.setVisible(not empty)
        if empty:
            if not self.library.folders("audio"):
                self.empty_title.setText("No audiobooks yet")
            elif self.search_box.text().strip():
                self.empty_title.setText("Nothing matches that search")
            else:
                self.empty_title.setText("Nothing on this shelf")
        if selected_id is not None and selected_id in self._row_by_id:
            self.view.setCurrentIndex(self.model.index(self._row_by_id[selected_id], 0))

    def refresh_book(self, book_id: int) -> None:
        row = self._row_by_id.get(book_id)
        if row is None:
            return
        fresh = self.library.book(book_id)
        if fresh is None:
            return
        item = self.model.item(row)
        if item is not None:
            item.setData(fresh, BOOK_ROLE)
            self.books[row] = fresh
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 0))
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        counts = self.library.book_counts()
        self.stats_label.setText(
            f"{counts['total']} books · {spoken_length(counts['seconds'])}\n"
            f"{counts['finished']} finished · {counts['listening']} in progress"
        )
        scope_name = dict((k, t) for k, t, _ in SCOPES)[self.scope]
        where = f" in {Path(self.root).name}" if self.root else ""
        self.status_label.setText(
            f"{len(self.books)} of {counts['total']} · {scope_name}{where}"
        )

    def _refresh_folders(self) -> None:
        while self.folder_box.count():
            item = self.folder_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        all_button = QPushButton("  All folders")
        all_button.setObjectName("NavItem")
        all_button.setCheckable(True)
        all_button.setChecked(self.root is None)
        all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        all_button.clicked.connect(lambda: self.set_root(None))
        self.folder_box.addWidget(all_button)
        for folder in self.library.folders("audio"):
            button = QPushButton(f"  {Path(folder).name or folder}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setChecked(self.root == folder)
            button.setToolTip(folder)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, f=folder: self.set_root(f))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, f=folder, b=button: self._folder_menu(f, b))
            self.folder_box.addWidget(button)

    # ---------------------------------------------------------------- actions

    def set_scope(self, scope: str) -> None:
        self.scope = scope
        for key, button in self.scope_buttons.items():
            button.setChecked(key == scope)
        self.reload()

    def set_root(self, root: str | None) -> None:
        self.root = root
        self.reload()

    def _on_sort_changed(self) -> None:
        self.library.set("book_sort", self.sort_box.currentData())
        self.reload()

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add a folder of audiobooks", str(Path.home()))
        if folder:
            self.folderAdded.emit(folder)

    def _folder_menu(self, folder: str, anchor: QWidget) -> None:
        menu = QMenu(self)
        open_action = menu.addAction("Open in file manager")
        open_action.triggered.connect(lambda: subprocess.Popen(["xdg-open", folder]))
        menu.addSeparator()
        remove = menu.addAction("Remove folder from the shelf")
        remove.triggered.connect(lambda: self._remove_folder(folder))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _remove_folder(self, folder: str) -> None:
        self.library.remove_folder(folder, kind="audio")
        if self.root == folder:
            self.root = None
        self.reload()

    def _open_overflow_menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        add = menu.addAction("Add folder…")
        add.triggered.connect(self.choose_folder)
        rescan = menu.addAction("Rescan folders")
        rescan.triggered.connect(self.rescanRequested)
        forget = menu.addAction("Forget books that are gone")
        forget.triggered.connect(self._purge_missing)
        menu.addSeparator()
        rebuild = menu.addAction("Rebuild library…")
        rebuild.triggered.connect(self.rebuildRequested)
        tidy = menu.addAction("Tidy up the books…")
        tidy.setToolTip("Set titles, authors and series right, and put the files in order")
        tidy.triggered.connect(self.tidyRequested)
        quiet = menu.addAction("Tidy new books as they are added")
        quiet.setCheckable(True)
        quiet.setChecked(bool(self.library.get("tidy_new_books", False)))
        quiet.setToolTip("Sets the names right in Aerwave only; your files are left alone")
        quiet.toggled.connect(lambda on: self.library.set("tidy_new_books", bool(on)))
        art = menu.addAction("Look up missing covers online")
        art.setCheckable(True)
        art.setChecked(bool(self.library.get("fetch_book_art", True)))
        art.setToolTip("When a book has no artwork of its own, ask the catalogue.\n"
                       "Only the book's name is sent, and only for books without art.")
        art.toggled.connect(self._set_fetch_art)
        glass_menu(menu, self.library, self.glassChanged.emit)
        menu.addSeparator()
        shortcuts = menu.addAction("Keyboard shortcuts")
        shortcuts.triggered.connect(self.helpRequested)
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _set_fetch_art(self, on: bool) -> None:
        self.library.set("fetch_book_art", bool(on))
        if on:
            # Books written off while this was off deserve another go.
            self.covers.retry_missed()
            self.reload()

    def _purge_missing(self) -> None:
        removed = self.library.purge_missing_books()
        self.reload()
        self.status_label.setText(
            f"Removed {removed} missing book{'s' if removed != 1 else ''}"
            if removed else "Nothing missing — every book is still on disk")

    def _play(self, index: QModelIndex, resume: bool = True) -> None:
        book = index.data(BOOK_ROLE)
        if book:
            self.playRequested.emit(book, resume)

    def play_current(self, resume: bool = True) -> None:
        index = self.view.currentIndex()
        if index.isValid():
            self._play(index, resume)
        elif self.books:
            self.view.setCurrentIndex(self.model.index(0, 0))
            self._play(self.model.index(0, 0), resume)

    def focus_search(self) -> None:
        self.search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_box.selectAll()

    def _open_context_menu(self, position: QPoint) -> None:
        index = self.view.indexAt(position)
        if not index.isValid():
            return
        book = index.data(BOOK_ROLE)
        menu = QMenu(self)
        elapsed = book.get("elapsed") or 0
        if elapsed > 30 and not book.get("finished"):
            resume = menu.addAction(f"Resume at {format_time(elapsed, True)}")
            resume.triggered.connect(lambda: self.playRequested.emit(book, True))
        start = menu.addAction("Listen from the beginning")
        start.triggered.connect(lambda: self.playRequested.emit(book, False))
        menu.addSeparator()

        finished = bool(book.get("finished"))
        toggle = menu.addAction("Mark as unfinished" if finished else "Mark as finished")
        toggle.triggered.connect(lambda: self._set_finished(book, not finished))
        if elapsed > 30:
            clear = menu.addAction("Forget my place")
            clear.triggered.connect(lambda: self._reset(book))
        menu.addSeparator()

        cover = menu.addAction("Find cover art…")
        cover.setToolTip("Search the catalogue, or choose a picture of your own")
        cover.triggered.connect(lambda: self.choose_cover(book))
        again = menu.addAction("Look for cover art again")
        again.setToolTip("Read the artwork beside or inside the file once more")
        again.triggered.connect(lambda: self._refresh_cover(book))
        reveal = menu.addAction("Show in file manager")
        target = book["path"] if Path(book["path"]).is_dir() else str(Path(book["path"]).parent)
        reveal.triggered.connect(lambda: subprocess.Popen(["xdg-open", target]))
        copy = menu.addAction("Copy path")
        copy.triggered.connect(lambda: QApplication.clipboard().setText(book["path"]))
        menu.addSeparator()
        remove = menu.addAction("Remove from the shelf")
        remove.triggered.connect(lambda: self._remove(book))
        menu.exec(self.view.viewport().mapToGlobal(position))

    def _set_finished(self, book: dict, finished: bool) -> None:
        self.library.set_book_finished(book["id"], finished)
        self.refresh_book(book["id"])
        if self.scope != "all":
            self.reload()

    def _reset(self, book: dict) -> None:
        self.library.reset_book_progress(book["id"])
        self.refresh_book(book["id"])
        if self.scope != "all":
            self.reload()

    def _refresh_cover(self, book: dict) -> None:
        self.covers.forget(book)
        self.covers.request(book, online=True)
        self.refresh_book(book["id"])

    def choose_cover(self, book: dict) -> None:
        """Pick a cover for one book, from the catalogue or from disk."""
        from .cover_view import CoverDialog        # noqa: PLC0415  (network, when asked)
        current = self.covers.get(book) if self.covers.has_artwork(book) else None
        dialog = CoverDialog(book, current, self)
        dialog.chosen.connect(lambda source: self._adopt_cover(book, source))
        dialog.exec()

    def _adopt_cover(self, book: dict, source: str) -> None:
        self.covers.adopt(book, source)
        self.status_label.setText(f"Setting the cover for {book.get('title') or 'the book'}…")

    def _remove(self, book: dict) -> None:
        self.library.remove_book(book["id"])
        self.reload()

    def _on_cover_ready(self, book_id: int) -> None:
        row = self._row_by_id.get(book_id)
        if row is not None:
            index = self.model.index(row, 0)
            self.model.dataChanged.emit(index, index)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
