"""Choosing a cover for a book, when the one it has is wrong or missing.

Most books never need this: there is a picture beside the file, or one inside
it, and failing both the catalogue usually knows the book. This is for the
rest — the recording ripped without art, the obscure title the catalogue has
under a different name, the omnibus whose cover is the wrong volume.

Searching and fetching both happen on a worker, and the thumbnails arrive as
they download, so the window is never waiting on the network.
"""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from . import book_lookup, covers, style
from .icons import icon

THUMB = 132
CAPTION = 2                            # lines under each tile, so rows line up
PICTURES = "Pictures (*.jpg *.jpeg *.png *.webp *.bmp);;All files (*)"
ART_ROLE = Qt.ItemDataRole.UserRole + 1


def _two_lines(name: str, author: str, width: int = 26) -> str:
    """A caption that is always two lines, so every tile is the same height.

    Catalogue names run long — "Oathbringer : The Stormlight Archive 3
    (Stormlight Archive)" — and left to wrap they push the rows about. The
    full text is in the tooltip.
    """
    name = " ".join((name or "Untitled").split())
    if len(name) > width:
        name = name[:width - 1].rstrip(" ,:-") + "…"
    author = " ".join((author or "").split())
    if len(author) > width:
        author = author[:width - 1].rstrip(" ,:-") + "…"
    return f"{name}\n{author}"


class _Signals(QObject):
    found = Signal(str, list)          # the query it answers, the matches
    thumb = Signal(str, object)        # art url, pixmap (None when it would not load)


class _SearchJob(QRunnable):
    def __init__(self, book: dict, query: str, signals: _Signals):
        super().__init__()
        self.book, self.query, self.signals = dict(book), query, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            found = book_lookup.candidates(self.book, self.query)
        except Exception:                          # noqa: BLE001  (offline, changed API)
            found = []
        try:
            self.signals.found.emit(self.query, found)
        except RuntimeError:
            pass                                   # the dialog closed while we looked


class _ThumbJob(QRunnable):
    """One preview, fetched at thumbnail size rather than full."""

    def __init__(self, url: str, signals: _Signals):
        super().__init__()
        self.url, self.signals = url, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        small = book_lookup._big(self.url, 200) or self.url
        scratch = covers.download(small)
        pixmap = None
        if scratch is not None:
            loaded = QPixmap(str(scratch))
            scratch.unlink(missing_ok=True)
            if not loaded.isNull():
                pixmap = loaded.scaled(THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
        try:
            self.signals.thumb.emit(self.url, pixmap)
        except RuntimeError:
            pass


class CoverDialog(QDialog):
    """Search the catalogue for a cover, or point at a picture on disk."""

    chosen = Signal(str)               # the picture to use: an address or a path

    def __init__(self, book: dict, current: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.book = dict(book)
        self.setWindowTitle("Cover art")
        self.setMinimumSize(760, 640)
        self._query = ""

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(12)

        heading = QLabel(book.get("title") or "This book")
        heading.setObjectName("EmptyTitle")
        heading.setWordWrap(True)
        column.addWidget(heading)

        top = QHBoxLayout()
        top.setSpacing(16)
        if current is not None and not current.isNull():
            now = QVBoxLayout()
            now.setSpacing(6)
            label = QLabel("On the shelf now")
            label.setObjectName("SectionLabel")
            now.addWidget(label)
            picture = QLabel()
            picture.setObjectName("CoverArt")
            picture.setFixedSize(THUMB, THUMB)
            picture.setPixmap(current.scaled(THUMB, THUMB,
                                             Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            now.addWidget(picture)
            now.addStretch(1)
            top.addLayout(now)

        right = QVBoxLayout()
        right.setSpacing(8)
        blurb = QLabel(
            "Aeris searches the audiobook and ebook catalogues. Change the words if "
            "the book is listed under another name, or use a picture of your own.")
        blurb.setObjectName("EmptyBody")
        blurb.setWordWrap(True)
        right.addWidget(blurb)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setPlaceholderText("Title and author")
        self.search_box.setText(self._opening_query())
        self.search_box.returnPressed.connect(self.search)
        row.addWidget(self.search_box, 1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        right.addLayout(row)
        right.addStretch(1)
        top.addLayout(right, 1)
        column.addLayout(top)

        self.grid = QListWidget()
        self.grid.setObjectName("CoverGrid")
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setFrameShape(QFrame.Shape.NoFrame)
        self.grid.setIconSize(QSize(THUMB, THUMB))
        self.grid.setGridSize(QSize(THUMB + 34, THUMB + 58))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setSpacing(6)
        self.grid.setUniformItemSizes(True)
        self.grid.itemSelectionChanged.connect(self._selected)
        self.grid.itemDoubleClicked.connect(lambda _item: self._use())
        column.addWidget(self.grid, 1)

        buttons = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("HintLabel")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        pick = QPushButton("  Use a picture…")
        pick.setIcon(icon("folder_add", 16, style.TEXT))
        pick.setToolTip("Choose an image file from this computer")
        pick.clicked.connect(self._from_disk)
        buttons.addWidget(pick)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.use_button = QPushButton("Use this cover")
        self.use_button.setObjectName("Primary")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._use)
        buttons.addWidget(self.use_button)
        column.addLayout(buttons)

        self._signals = _Signals(self)
        self._signals.found.connect(self._show_results)
        self._signals.thumb.connect(self._show_thumb)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._rows: dict[str, QListWidgetItem] = {}
        self.search()

    # ----------------------------------------------------------------- search

    def _opening_query(self) -> str:
        title = book_lookup.shelf_title(self.book) or (self.book.get("title") or "")
        author = (self.book.get("author") or "").strip()
        return " ".join(f"{title} {author}".split())

    def search(self) -> None:
        self._query = self.search_box.text().strip()
        self.grid.clear()
        self._rows.clear()
        self.use_button.setEnabled(False)
        self.search_button.setEnabled(False)
        self.status.setText("Searching…")
        self._pool.start(_SearchJob(self.book, self._query, self._signals))

    def _show_results(self, query: str, found: list) -> None:
        if query != self._query:
            return                      # an older search answering late
        self.search_button.setEnabled(True)
        if not found:
            self.status.setText(
                "Nothing came back. Try the title on its own, or the author's name — "
                "or use a picture of your own.")
            return
        self.status.setText(f"{len(found)} to choose from. "
                            "Double-click one, or select it and press Use.")
        for match in found:
            item = QListWidgetItem(_two_lines(match.name, match.author))
            item.setData(ART_ROLE, match.art)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setToolTip(f"{match.name}\n{match.author}"
                            + (f"\n{match.year}" if match.year else ""))
            item.setSizeHint(QSize(THUMB + 34, THUMB + 58))
            self.grid.addItem(item)
            self._rows[match.art] = item
            self._pool.start(_ThumbJob(match.art, self._signals))

    def _show_thumb(self, url: str, pixmap) -> None:
        item = self._rows.get(url)
        if item is None:
            return
        if pixmap is None:
            item.setToolTip(item.toolTip() + "\n(the picture would not load)")
            return
        item.setIcon(pixmap)

    # ------------------------------------------------------------------ using

    def _selected(self) -> None:
        self.use_button.setEnabled(bool(self.grid.selectedItems()))

    def _use(self) -> None:
        picked = self.grid.selectedItems()
        if not picked:
            return
        self.chosen.emit(str(picked[0].data(ART_ROLE)))
        self.accept()

    def _from_disk(self) -> None:
        start = self.book.get("path") or ""
        folder = start if Path(start).is_dir() else str(Path(start).parent)
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choose a cover", folder, PICTURES)
        if path:
            self.chosen.emit(path)
            self.accept()

    def reject(self) -> None:
        self._pool.clear()
        super().reject()
