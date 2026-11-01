"""The book's own text, beside the narration — an audiobook's synced lyrics.

The panel shows one chapter as lines, lights up the line being read and keeps
it centred, exactly as the lyrics panel does for a song. Click a line and the
narration goes there.

An EPUB carries no timings, so they have to come from the narration itself.
*Find my place* transcribes a stretch of it with the same engine Aerwave uses for
subtitles and matches it to the text — and because the audio is read from the
file rather than off the speaker, the stretch runs well past the playhead. What
comes back is not one anchor but a series of them: a real time for every
sentence in the next minute or so, which the panel interpolates between. Past
the end of that stretch it carries on at the pace those timings measured, until
the next listen picks up where they left off.

Every chapter start is checked this way too, rather than trusted: the offset
between the two chapter lists is a guess, and a boundary is where a guess goes
wrong.

Nothing here needs to know about playback speed. mpv counts position in the
file's own seconds, so at 2x those seconds simply arrive twice as fast, and
because the listening is scheduled by how much narration is timed rather than
by the clock, it keeps up on its own.
"""

import bisect
import re
from pathlib import Path

from PySide6.QtCore import (QEvent, QObject, QPoint, QPointF, QRunnable, QThreadPool,
                            QTimer, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QTextLayout, QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import dictionary, epub, style, subtitles
from .icons import icon

TEXT_SIZES = (13, 15, 16, 18, 20, 23, 26, 30)
DEFAULT_SIZE = 16
LISTEN_SECONDS = 75.0      # how much narration to transcribe in one go
LISTEN_LEAD = 8.0          # begin a little before now, so a sentence is not clipped
LISTEN_MARGIN = 10.0       # listen again this long before the timings run out
LISTEN_GAP = 20.0          # never listen twice inside this much narration
CHAPTER_SETTLE_MS = 1200   # let a chapter change settle before listening to it
DEFAULT_LEAD = 0.6         # light a line up a moment before the voice reaches it
LEAD_STEP = 0.2
LEAD_RANGE = (-3.0, 3.0)
SANE_RATES = (0.8, 8.0)    # words per second a human narrator can manage
WORD_ROLE = Qt.ItemDataRole.UserRole + 1
ITEM_PAD_X, ITEM_PAD_Y = 10, 5     # the item padding the stylesheet draws text inside
_WORDISH = re.compile(r"[\w\u2019'\-]")


def word_around(text: str, index: int) -> str:
    """The whole word that the character at `index` belongs to."""
    if not text:
        return ""
    index = max(0, min(index, len(text) - 1))
    if not _WORDISH.match(text[index]):
        index = max(0, index - 1)      # the gap after a word still means that word
        if not _WORDISH.match(text[index]):
            return ""
    start = end = index
    while start > 0 and _WORDISH.match(text[start - 1]):
        start -= 1
    while end + 1 < len(text) and _WORDISH.match(text[end + 1]):
        end += 1
    return text[start:end + 1]


def _button(name: str, tip: str, size: int = 16) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size, style.TEXT))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedSize(size + 14, size + 14)
    return button


class _Signals(QObject):
    loaded = Signal(int, object, str)             # book, Ebook, the file it came from
    defined = Signal(str, object)                 # word, dictionary Entry
    located = Signal(int, int, object, float, float)  # book, chapter, timings, share
    trouble = Signal(int, str)
    searched = Signal(int, str, list)             # book, query, [(chapter, block)]


class _ReadJob(QRunnable):
    """Hunting for an EPUB and parsing it are both disk work, so they run here."""

    def __init__(self, book: dict, known: str, signals: _Signals):
        super().__init__()
        self.book = dict(book)
        self.known = known
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        path = self.known
        if not path or not Path(path).is_file():
            found = epub.search(self.book)
            path = str(found) if found else ""
        text = epub.read(path) if path else epub.Ebook()
        self.signals.loaded.emit(self.book["id"], text, path)


class _ListenJob(QRunnable):
    """Transcribe a moment of narration, then find that passage in the text."""

    def __init__(self, book_id: int, source: str, offset: float, at_elapsed: float,
                 chapters: list, near: int, model: str, signals: _Signals):
        super().__init__()
        self.book_id = book_id
        self.source = source
        self.offset = offset          # where the clip starts inside the file
        self.at_elapsed = at_elapsed  # the same moment, counted from the book's start
        self.chapters = chapters
        self.near = near
        self.model = model
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        begin = max(0.0, self.offset)
        try:
            heard = subtitles.transcribe_moment(self.source, begin, LISTEN_SECONDS,
                                                self.model)
        except Exception as exc:                      # noqa: BLE001  (engine's own errors)
            self.signals.trouble.emit(self.book_id, f"listening failed: {exc}")
            return
        if not heard:
            self.signals.trouble.emit(self.book_id, "nothing was said in that stretch")
            return
        chapter, word, share = epub.locate(subtitles.spoken_text(heard), self.chapters,
                                           near=self.near)
        if chapter < 0 or share < epub.CONFIDENT:
            self.signals.trouble.emit(
                self.book_id, "could not find those words in the text — is this the "
                              "right book, or an abridged reading?")
            return
        # Time every segment, not just the first. Most of the clip lies ahead of
        # the playhead — it is read from the file, not off the speaker — so this
        # says when the coming lines will be spoken, not merely where we are.
        points = epub.timeline(self.chapters[chapter], heard, self.at_elapsed, word)
        if not points:
            points = [(word, self.at_elapsed + heard[0][1])]
        self.signals.located.emit(self.book_id, chapter, points, share, 0.0)


class _SearchJob(QRunnable):
    """Searching a long book means touching every chapter, so not on the GUI thread."""

    LIMIT = 500

    def __init__(self, book_id: int, query: str, chapters: list, signals: _Signals):
        super().__init__()
        self.book_id = book_id
        self.query = query
        self.chapters = chapters
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        needle = self.query.lower()
        hits: list[tuple[int, int]] = []
        for index, chapter in enumerate(self.chapters):
            for position, block in enumerate(chapter.blocks):
                if needle in epub.plain_text(block).lower():
                    hits.append((index, position))
                    if len(hits) >= self.LIMIT:
                        self.signals.searched.emit(self.book_id, self.query, hits)
                        return
        self.signals.searched.emit(self.book_id, self.query, hits)


class _LookupJob(QRunnable):
    """One dictionary lookup, off the GUI thread."""

    def __init__(self, word: str, signals: _Signals):
        super().__init__()
        self.word, self.signals = word, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        entry = dictionary.look_up(self.word)
        try:
            self.signals.defined.emit(self.word, entry)
        except RuntimeError:
            pass                       # the window closed while we looked


class DefinitionPopup(QFrame):
    """What a word means, in a panel over the page, the way an e-reader does it."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("DefinitionPopup")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(16, 13, 16, 14)
        self._column.setSpacing(7)
        # A definite width: wrapped labels asked to size themselves collapse to
        # something tall and narrow, which is no way to read a definition.
        self.setFixedWidth(430)

    def _clear(self) -> None:
        while self._column.count():
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Out of the tree at once: deleteLater() alone leaves the old
                # labels painting themselves over the new ones until it runs.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _line(self, text: str, name: str = "", wrap: bool = True) -> QLabel:
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        label.setWordWrap(wrap)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._column.addWidget(label)
        return label

    def waiting(self, word: str) -> None:
        self._clear()
        self._line(word, "DefinitionWord")
        self._line("Looking it up…", "HintLabel")
        self.adjustSize()

    def show_entry(self, entry) -> None:
        self._clear()
        self._line(entry.word, "DefinitionWord")
        if entry.note:
            self._line(entry.note, "HintLabel")
        if not entry:
            self._line(entry.error or "Nothing found.", "EmptyBody")
        for number, sense in enumerate(entry.senses, 1):
            part = f"<i>{sense.part.lower()}</i>  " if sense.part else ""
            self._line(f"{number}. {part}{sense.text}", "DefinitionSense")
            if sense.example:
                self._line(f"“{sense.example}”", "DefinitionExample")
        self.adjustSize()

    def show_near(self, where: QPoint) -> None:
        """Open beside the word, kept on the screen it belongs to."""
        self.adjustSize()
        screen = QApplication.screenAt(where) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(area.left() + 8, where.x() - self.width() // 3),
                area.right() - self.width() - 8)
        y = where.y() + 18
        if y + self.height() > area.bottom() - 8:
            y = max(area.top() + 8, where.y() - self.height() - 14)
        self.move(x, y)
        self.show()


class ReaderPanel(QWidget):
    """The text of the book, following the narration line by line."""

    seekRequested = Signal(float)          # take the narration to this second
    statusChanged = Signal(str)            # something worth a line in the toast

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.book: dict | None = None
        self.text = epub.Ebook()
        self.lines: list = []
        self.audio_chapters: list = []
        self.audio_chapter = 0
        self.elapsed = 0.0
        self.source = ""                   # the file playing, for the transcriber
        self.source_offset = 0.0           # where we are inside that file
        self.anchors: dict[int, tuple] = {}   # audio chapter -> (word, second)
        self.rate = 0.0                    # words per second, learned by listening
        self.timings: list = []            # (word, second) heard, for this chapter
        self.timings_chapter = -1
        self.local_rate = 0.0              # the pace measured across those timings
        self._last_listen = -1e9
        self._before: list[int] = []       # words of the book before each chapter
        self._last_listen = -1e9
        self._quiet_listen = False
        self.shift = 0
        self.aligned = False
        self.showing = -1
        self.current_row = -1
        self._row_words: list[int] = []
        self._following = True
        self._loading_for = -1
        self._listening = False
        self._matches: list = []
        self._match = -1

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addLayout(self._build_header())
        column.addWidget(self._build_search())

        self.pages = QStackedWidget()
        self.list = QListWidget()
        self.list.setObjectName("ReaderText")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setWordWrap(True)
        # With wrapping on, a horizontal bar means a line that refuses to wrap;
        # turning it off makes long sentences fold into the width instead.
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(self._line_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._line_menu)
        self.pages.addWidget(self.list)
        self.pages.addWidget(self._build_notice())
        column.addWidget(self.pages, 1)

        # A chapter start is checked against the narration rather than trusted,
        # after a pause so that scrubbing through chapters does not set off one
        # listen per chapter crossed.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(CHAPTER_SETTLE_MS)
        self._settle.timeout.connect(self._confirm_chapter)

        self._lead: float | None = None     # read from the settings on first use
        self._auto_anchor: bool | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _Signals(self)
        self._signals.loaded.connect(self._loaded)
        self._signals.located.connect(self._found_place)
        self._signals.trouble.connect(self._listen_failed)
        self._signals.searched.connect(self._searched)
        self._signals.defined.connect(self._defined)
        self._popup = DefinitionPopup(self)
        self._looking_up = ""
        self._apply_text_size()
        self._say("Nothing open yet.")

    # ------------------------------------------------------------------ build

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(3)
        self.chapter_label = QLabel("")
        self.chapter_label.setObjectName("SectionLabel")
        header.addWidget(self.chapter_label, 1)

        self.smaller_button = QPushButton("A−")
        self.bigger_button = QPushButton("A+")
        for button, step, tip in ((self.smaller_button, -1, "Smaller text  (−)"),
                                  (self.bigger_button, 1, "Bigger text  (+)")):
            button.setObjectName("Flat")
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(30, 30)
            button.clicked.connect(lambda _checked=False, s=step: self.step_size(s))
            header.addWidget(button)

        self.search_button = _button("search", "Search the book  (/)")
        self.search_button.clicked.connect(self._toggle_search)
        header.addWidget(self.search_button)

        self.listen_button = _button("subtitles", "Find my place — listen to a minute "
                                                  "and match it to the text")
        self.listen_button.clicked.connect(self.find_my_place)
        header.addWidget(self.listen_button)

        self.follow_button = QPushButton("  Following")
        self.follow_button.setObjectName("NavItem")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setIcon(icon("sync", 14, style.ACCENT))
        self.follow_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.follow_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.follow_button.setToolTip("Keep the text in step with the narration.\n"
                                      "Turning pages by hand switches this off.")
        self.follow_button.clicked.connect(self._follow_toggled)
        header.addWidget(self.follow_button)

        self.previous_button = _button("prev", "The chapter before  (,)")
        self.previous_button.clicked.connect(lambda: self.turn(-1))
        self.next_button = _button("next", "The chapter after  (.)")
        self.next_button.clicked.connect(lambda: self.turn(1))
        self.menu_button = _button("gear", "Text settings", 15)
        self.menu_button.clicked.connect(self._open_menu)
        for widget in (self.previous_button, self.next_button, self.menu_button):
            header.addWidget(widget)
        return header

    def _build_search(self) -> QWidget:
        self.search_row = QWidget()
        row = QHBoxLayout(self.search_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Find a line anywhere in the book…")
        self.search_field.returnPressed.connect(lambda: self.step_match(1))
        self.search_field.textChanged.connect(self._search_changed)
        self.search_field.installEventFilter(self)
        row.addWidget(self.search_field, 1)
        self.match_label = QLabel("")
        self.match_label.setObjectName("HintLabel")
        row.addWidget(self.match_label)
        previous_match = _button("prev", "Previous match  (Shift+Enter)", 14)
        previous_match.clicked.connect(lambda: self.step_match(-1))
        next_match = _button("next", "Next match  (Enter)", 14)
        next_match.clicked.connect(lambda: self.step_match(1))
        close = _button("close", "Close the search  (Esc)", 14)
        close.clicked.connect(self._close_search)
        for widget in (previous_match, next_match, close):
            row.addWidget(widget)
        self.search_row.hide()
        return self.search_row

    def _build_notice(self) -> QWidget:
        empty = QWidget()
        column = QVBoxLayout(empty)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(10)
        self.notice = QLabel("")
        self.notice.setObjectName("EmptyBody")
        self.notice.setWordWrap(True)
        self.notice.setAlignment(Qt.AlignmentFlag.AlignTop)
        column.addWidget(self.notice)
        self.choose_button = QPushButton("Choose an EPUB…")
        self.choose_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choose_button.clicked.connect(self.choose_file)
        column.addWidget(self.choose_button, alignment=Qt.AlignmentFlag.AlignLeft)
        column.addStretch(1)
        return empty

    # ------------------------------------------------------------------ state

    @property
    def lead(self) -> float:
        """How far ahead of the voice the highlight runs, in seconds.

        A line lit as the narrator arrives at it reads as in time; one lit after
        the words are under way reads as late. How much of a lead feels right
        differs from person to person, so it is yours to set — the same nudge
        the video player gives subtitles.

        Read from the settings once and kept: this is asked for on every
        position tick, several times a second, and it changes only when you
        change it.
        """
        if self._lead is None:
            try:
                stored = float(self.library.get("reader_lead", DEFAULT_LEAD))
            except (TypeError, ValueError):
                stored = DEFAULT_LEAD
            self._lead = max(LEAD_RANGE[0], min(stored, LEAD_RANGE[1]))
        return self._lead

    @property
    def auto_anchor(self) -> bool:
        """Whether to keep checking the estimate against the narration.

        Kept in memory for the same reason as `lead`: this is consulted on
        every position tick.
        """
        if self._auto_anchor is None:
            self._auto_anchor = bool(self.library.get("reader_auto_anchor", True))
        return self._auto_anchor

    def nudge_lead(self, direction: int) -> None:
        """Move the text earlier or later against the voice."""
        if not self.text.chapters:
            return
        value = round(self.lead + LEAD_STEP * direction, 2)
        value = max(LEAD_RANGE[0], min(value, LEAD_RANGE[1]))
        self._lead = value
        self.library.set("reader_lead", value)
        if abs(value) < 0.05:
            self.statusChanged.emit("Text in step with the voice")
        else:
            self.statusChanged.emit(f"Text {abs(value):.1f}s "
                                    f"{'ahead of' if value > 0 else 'behind'} the voice")
        self.current_row = -1
        self._highlight(self._row_at(self._word_now()))

    @property
    def size_px(self) -> int:
        stored = self.library.get("reader_text_size", DEFAULT_SIZE)
        try:
            return max(TEXT_SIZES[0], min(int(stored), TEXT_SIZES[-1]))
        except (TypeError, ValueError):
            return DEFAULT_SIZE

    def load_for(self, book: dict) -> None:
        """Open the text for a book, hunting for it the first time around."""
        self.book = book
        self._settle.stop()
        self.text = epub.Ebook()
        self.lines = []
        self.anchors = {}
        self.timings, self.timings_chapter, self.local_rate = [], -1, 0.0
        self._before = []
        self.showing = -1
        self.current_row = -1
        self.audio_chapter = 0
        self._matches, self._match = [], -1
        self._close_search()
        self._set_following(True, quiet=True)
        if book is None:
            self._say("Nothing open yet.")
            return
        stored = self.library.book_text(book["id"])
        self.shift = int(stored.get("shift") or 0)
        self.aligned = bool(stored.get("aligned"))
        self.rate = float(stored.get("rate") or 0)
        self._last_listen = -1e9
        known = stored.get("path") or ""
        if not known and stored.get("looked"):
            # A search already came up empty; do not walk the disk on every open.
            self._say(self._nothing_found_text())
            return
        self._loading_for = book["id"]
        self._say("Looking for the text…")
        self._pool.start(_ReadJob(book, known, self._signals))

    def set_chapters(self, chapters: list) -> None:
        """The audio chapter list — titles to line up against, times to read by."""
        chapters = [dict(c) for c in chapters]
        if chapters == self.audio_chapters:
            return
        self.audio_chapters = chapters
        if self.text and not self.aligned:
            self.shift = epub.best_shift([c["title"] for c in chapters],
                                         self.text.chapters)
            self._remember(shift=self.shift)
        if self._following:
            self._show(self._wanted_index())

    def follow_audio(self, audio_index: int) -> None:
        """The narration has reached a new chapter."""
        if audio_index == self.audio_chapter:
            return
        self.audio_chapter = max(0, audio_index)
        if self._following:
            # Show where the offset says we are, then go and check: a chapter
            # boundary is exactly where an offset turns out to be wrong, and
            # the marks in a file are not evidence of anything.
            self._show(self._wanted_index())
            self._settle.start()

    def _confirm_chapter(self) -> None:
        """Work out which chapter this really is by listening to it."""
        if not (self._following and self.text.chapters and self.isVisible()):
            return
        if self._listening or not self.auto_anchor:
            return
        if not subtitles.engine_available():
            return
        self.find_my_place(quiet=True)

    def follow_position(self, elapsed: float, source: str = "", offset: float = 0.0) -> None:
        """Where the narration is now, called as it plays."""
        self.elapsed = float(elapsed)
        if source:
            self.source, self.source_offset = source, float(offset)
        if self._following and self.lines and self.isVisible():
            self._highlight(self._row_at(self._word_now()))
            self._maybe_reanchor()

    def _maybe_reanchor(self) -> None:
        """Check the estimate against the narration now and then.

        Each listen times the next stretch of narration, so the next one is due
        when those timings are about to run out — which is measured in narration,
        not in wall clock, and so takes care of playback speed by itself.
        """
        if abs(self.elapsed - self._last_listen) < LISTEN_GAP:
            return                               # not twice in the same breath
        if self._listening or not self.auto_anchor:
            return
        if self._covered_until() - self.elapsed > LISTEN_MARGIN:
            return                               # there are real timings ahead still
        if not subtitles.engine_available():
            self._last_listen = self.elapsed     # nothing to do until it is installed
            return
        self.find_my_place(quiet=True)

    def _wanted_index(self) -> int:
        return epub.text_index(self.audio_chapter, self.shift, len(self.text.chapters))

    # ----------------------------------------------------------------- timing

    def _bounds(self) -> tuple:
        """When the audio chapter being read starts and ends."""
        if not self.audio_chapters:
            return 0.0, max(1.0, self.elapsed + 1.0)
        chapter = self.audio_chapters[min(self.audio_chapter, len(self.audio_chapters) - 1)]
        return float(chapter.get("start") or 0), float(chapter.get("end") or 0)

    def _rate(self) -> float:
        """Words per second of narration — how fast the text is being used up.

        This is deliberately not a per-chapter figure. Dividing one chapter's
        words by one audio chapter's length only works if the two lists agree,
        and plenty of audiobooks carry chapter marks that have nothing to do
        with the book's own chapters. The whole book's average survives that,
        and listening once replaces the average with the real thing.

        Playback speed does not enter into it: mpv reports position in the
        file's own seconds, so a book read at 2x covers these seconds twice as
        fast and the sums still hold.
        """
        if self.rate > 0:
            return self.rate
        whole = self._book_rate()
        if whole > 0:
            return whole
        start, end = self._bounds()
        words = 1
        if 0 <= self.showing < len(self.text.chapters):
            words = max(1, self.text.chapters[self.showing].words)
        return words / max(1.0, end - start)

    def _book_rate(self) -> float:
        """The average over the whole book, which no chapter mark can distort."""
        if not self.text.chapters or not self.audio_chapters:
            return 0.0
        words = sum(chapter.words for chapter in self.text.chapters)
        seconds = float(self.audio_chapters[-1].get("end") or 0)
        return words / seconds if words > 0 and seconds > 60 else 0.0

    def _anchor(self) -> tuple:
        return self.anchors.get(self.audio_chapter, (0, self._bounds()[0]))

    def _timings(self) -> list:
        """The heard timings, if they belong to the chapter on screen."""
        return self.timings if self.timings_chapter == self.showing else []

    def _pace(self) -> float:
        return self.local_rate if self.local_rate > 0 else self._rate()

    def _word_now(self) -> int:
        """Which word is being spoken now.

        Inside a stretch that was actually listened to, this is interpolation
        between two known moments rather than an estimate at all. It reads a
        fraction of a second ahead of the voice on purpose: a line that lights
        up as it is being started reads as in time, while one that waits until
        the words are under way reads as late.
        """
        now = self.elapsed + self.lead
        points = self._timings()
        if points:
            if points[0][1] <= now <= points[-1][1]:
                after = bisect.bisect_left([when for _w, when in points], now)
                after = max(1, min(after, len(points) - 1))
                (word_before, before), (word_after, when) = points[after - 1], points[after]
                share = (now - before) / max(0.01, when - before)
                return max(0, int(word_before + (word_after - word_before) * share))
            edge = points[-1] if now > points[-1][1] else points[0]
            return max(0, int(edge[0] + (now - edge[1]) * self._pace()))
        word, when = self._anchor()
        return max(0, int(word + (now - when) * self._rate()))

    def _time_of_word(self, target: int) -> float:
        start, end = self._bounds()
        points = self._timings()
        if points:
            if points[0][0] <= target <= points[-1][0]:
                after = bisect.bisect_left([word for word, _w in points], target)
                after = max(1, min(after, len(points) - 1))
                (word_before, before), (word_after, when) = points[after - 1], points[after]
                share = (target - word_before) / max(1, word_after - word_before)
                return before + (when - before) * share
            edge = points[-1] if target > points[-1][0] else points[0]
            return max(0.0, edge[1] + (target - edge[0]) / max(0.01, self._pace()))
        word, when = self._anchor()
        seconds = when + (target - word) / max(0.01, self._rate())
        return max(start, min(seconds, max(start, end - 1.0)))

    def _row_at(self, word: int) -> int:
        if not self._row_words:
            return -1
        row = max(0, bisect.bisect_right(self._row_words, word) - 1)
        while row < len(self.lines) and self.lines[row].blank:
            row += 1
        return min(row, len(self.lines) - 1)

    # ----------------------------------------------------------------- loading

    def _loaded(self, book_id: int, text, path: str) -> None:
        if self.book is None or book_id != self.book["id"] or book_id != self._loading_for:
            return
        self.text = text
        running = 0
        self._before = []
        for chapter in self.text.chapters:
            self._before.append(running)
            running += chapter.words
        self.library.set_book_text(book_id, path=path if text else "", looked=True)
        if not text:
            trouble = f"{text.error}\n\n" if text.error else ""
            self._say(trouble + self._nothing_found_text())
            return
        if not self.aligned and self.audio_chapters:
            self.shift = epub.best_shift([c["title"] for c in self.audio_chapters],
                                         self.text.chapters)
            self._remember(shift=self.shift)
        self._show(self._wanted_index())
        self.statusChanged.emit(
            f"Reading along with {self.text.title or Path(path).stem}")

    def _nothing_found_text(self) -> str:
        where = ""
        if self.book is not None:
            source = Path(self.book["path"])
            where = (f"\n\nAerwave looked beside the book, in {source.parent.name}, and "
                     f"through the folders around your audiobook library.")
        return ("No text for this book yet." + where +
                "\n\nPick the EPUB yourself and Aerwave will remember it.")

    # ----------------------------------------------------------------- showing

    def _show(self, index: int, keep_row: bool = False) -> None:
        if not self.text.chapters:
            return
        index = max(0, min(index, len(self.text.chapters) - 1))
        if index == self.showing:
            return
        self.showing = index
        self.lines = epub.lines_of(self.text.chapters[index])
        self._row_words = [line.start_word for line in self.lines]
        self.current_row = -1
        self.list.clear()
        for line in self.lines:
            item = QListWidgetItem(" " if line.blank else f"  {line.text}")
            item.setData(WORD_ROLE, line.start_word)
            item.setForeground(QColor(style.MUTED))
            if line.blank:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            elif line.heading:
                font = QFont(item.font())
                font.setWeight(QFont.Weight.Bold)
                item.setFont(font)
            self.list.addItem(item)
        self.pages.setCurrentIndex(0)
        if not keep_row:
            self.list.scrollToTop()
        self._refresh_header()
        if self._following:
            self._highlight(self._row_at(self._word_now()))

    def _highlight(self, row: int) -> None:
        if row < 0 or row >= self.list.count() or row == self.current_row:
            return
        for target, colour, weight in ((self.current_row, style.MUTED, QFont.Weight.Normal),
                                       (row, style.ACCENT, QFont.Weight.DemiBold)):
            item = self.list.item(target) if 0 <= target < self.list.count() else None
            if item is None:
                continue
            font = QFont(item.font())
            font.setWeight(QFont.Weight.Bold if self.lines[target].heading else weight)
            item.setFont(font)
            item.setForeground(QColor(colour))
        self.current_row = row
        self.list.scrollToItem(self.list.item(row),
                               QAbstractItemView.ScrollHint.PositionAtCenter)

    def _refresh_header(self) -> None:
        if not self.text.chapters or not 0 <= self.showing < len(self.text.chapters):
            return
        chapter = self.text.chapters[self.showing]
        self.chapter_label.setText(
            f"{chapter.title}   ·   {self.showing + 1} of {len(self.text.chapters)}")
        self.chapter_label.setToolTip(chapter.title)
        behind = self.showing != self._wanted_index()
        self.follow_button.setText("  Catch up" if behind and not self._following
                                   else "  Following" if self._following else "  Reading on")

    def _say(self, message: str) -> None:
        self.notice.setText(message)
        self.pages.setCurrentIndex(1)
        self.chapter_label.setText("")

    def turn(self, direction: int) -> None:
        """Page through the text without moving the narration."""
        if not self.text.chapters:
            return
        target = self.showing + direction
        if not 0 <= target < len(self.text.chapters):
            return
        if target != self._wanted_index():
            self._set_following(False)
        self._show(target)

    def _line_clicked(self, item: QListWidgetItem) -> None:
        """Clicking a line takes the narration to it, the way a lyric does."""
        word = item.data(WORD_ROLE)
        if word is None or not self.audio_chapters:
            return
        if self.showing != self._wanted_index():
            # Reading a different chapter: go to the start of its narration first.
            audio = max(0, min(self.showing - self.shift, len(self.audio_chapters) - 1))
            self.audio_chapter = audio
            self.seekRequested.emit(float(self.audio_chapters[audio]["start"]))
            self._set_following(True, quiet=True)
            return
        self.seekRequested.emit(self._time_of_word(int(word)))
        self._set_following(True, quiet=True)

    # ------------------------------------------------------------ finding you

    def find_my_place(self, quiet: bool = False) -> None:
        """Listen to the narration and pin the text to it."""
        if self.book is None or not self.text.chapters:
            return
        if self._listening:
            if not quiet:
                self.statusChanged.emit("Still listening…")
            return
        if not subtitles.engine_available():
            if quiet:
                return              # never interrupt listening with a download offer
            dialog = subtitles.SetupDialog(self)
            dialog.exec()
            if not dialog.installed:
                return
        source = self.source or self.book["path"]
        # Carry on from where the last stretch of timings ran out, so the two
        # meet up instead of transcribing the same words twice.
        from_elapsed = max(self.elapsed - LISTEN_LEAD, self._covered_until() - 3.0)
        self._listening = True
        self._quiet_listen = quiet
        self._last_listen = self.elapsed
        self.listen_button.setIcon(icon("subtitles", 16, style.ACCENT))
        if not quiet:
            self.statusChanged.emit("Listening to the narration…")
        self._pool.start(_ListenJob(
            self.book["id"], source, self._file_offset(from_elapsed), from_elapsed,
            self.text.chapters, self._wanted_index(),
            str(self.library.get("whisper_model", subtitles.DEFAULT_MODEL)),
            self._signals))

    def _file_offset(self, when: float) -> float:
        """A moment in the book, as a position inside the file now playing."""
        return max(0.0, when - (self.elapsed - self.source_offset))

    def _covered_until(self) -> float:
        """When the heard timings run out; now, if there are none to speak of."""
        points = self._timings()
        if points and points[0][1] - 30 <= self.elapsed <= points[-1][1]:
            return points[-1][1]
        return self.elapsed

    def _found_place(self, book_id: int, chapter: int, points: list, share: float,
                     _spare: float) -> None:
        self._listening = False
        self.listen_button.setIcon(icon("subtitles", 16, style.TEXT))
        if self.book is None or book_id != self.book["id"] or not points:
            return
        word, at = points[0]
        # The text chapter now known to hold this moment fixes the alignment...
        self.shift = chapter - self.audio_chapter
        self.aligned = True
        self._remember(shift=self.shift, aligned=True)
        self._set_following(True, quiet=True)
        self._show(chapter)
        # ...and the timings say when each of the coming lines is spoken.
        line = epub.line_at_word(self.text.chapters[chapter], word)
        moved = abs(line - self.current_row)
        self.timings, self.timings_chapter = list(points), chapter
        self.anchors[self.audio_chapter] = (word, at)
        if len(points) >= 2:
            span = points[-1][1] - points[0][1]
            travelled = points[-1][0] - points[0][0]
            if span > 15 and travelled > 0:
                measured = travelled / span
                if SANE_RATES[0] <= measured <= SANE_RATES[1]:
                    self.local_rate = measured
                    self.rate = measured if self.rate <= 0 else self.rate * 0.5 + measured * 0.5
                    self._remember(rate=self.rate)
        self.current_row = -1
        self._highlight(self._row_at(self._word_now()))
        self._refresh_header()
        # An automatic check that changed almost nothing is not worth saying.
        if not self._quiet_listen or moved > 12:
            self.statusChanged.emit(
                f"Found your place — {self.text.chapters[chapter].title}")

    def _listen_failed(self, book_id: int, message: str) -> None:
        self._listening = False
        self.listen_button.setIcon(icon("subtitles", 16, style.TEXT))
        if self.book is not None and book_id == self.book["id"]:
            self.statusChanged.emit(message)

    # --------------------------------------------------------------- searching

    def open_search(self) -> None:
        """Show the search box and put the cursor in it."""
        self.search_row.show()
        self.search_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_field.selectAll()

    def eventFilter(self, watched, event):
        """Enter steps through matches and Escape closes the box, rather than
        leaving the book the way Escape does everywhere else in the player."""
        if watched is self.search_field and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._close_search()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step_match(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                                else 1)
                return True
        return super().eventFilter(watched, event)

    def _toggle_search(self) -> None:
        if self.search_row.isVisible():
            self._close_search()
        else:
            self.open_search()

    def _close_search(self) -> None:
        self.search_row.hide()
        self.match_label.setText("")

    def _search_changed(self, query: str) -> None:
        query = query.strip()
        self._matches, self._match = [], -1
        if len(query) < 3 or not self.text.chapters or self.book is None:
            self.match_label.setText("")
            return
        self.match_label.setText("…")
        self._pool.start(_SearchJob(self.book["id"], query, self.text.chapters,
                                    self._signals))

    def _searched(self, book_id: int, query: str, hits: list) -> None:
        if (self.book is None or book_id != self.book["id"]
                or query != self.search_field.text().strip()):
            return
        self._matches = hits
        self._match = -1
        if not hits:
            self.match_label.setText("nothing")
            return
        capped = "+" if len(hits) >= _SearchJob.LIMIT else ""
        self.match_label.setText(f"{len(hits)}{capped} found")
        self.step_match(1)

    def step_match(self, direction: int) -> None:
        """Jump to the next or previous match, wherever in the book it is."""
        if not self._matches:
            return
        self._match = (self._match + direction) % len(self._matches)
        chapter, block = self._matches[self._match]
        capped = "+" if len(self._matches) >= _SearchJob.LIMIT else ""
        self.match_label.setText(f"{self._match + 1} of {len(self._matches)}{capped}")
        if chapter != self.showing:
            self._set_following(False, quiet=True)
            self._show(chapter)
        row = self._row_of_text(self.search_field.text().strip(), block)
        if row >= 0:
            self.current_row = -1
            self._highlight(row)

    def _row_of_text(self, needle: str, block: int) -> int:
        """The line holding a match, preferring the paragraph it was found in."""
        needle = needle.lower()
        for row, line in enumerate(self.lines):
            if not line.blank and needle in line.text.lower():
                return row
        return min(block, len(self.lines) - 1) if self.lines else -1

    # -------------------------------------------------------------- dictionary

    def _word_at(self, point: QPoint) -> str:
        """The word under the cursor, laid out the way the list draws it."""
        item = self.list.itemAt(point)
        if item is None or not item.text().strip():
            return ""
        rect = self.list.visualItemRect(item)
        text = item.text()
        font = QFont(item.font())
        font.setPixelSize(self.size_px)     # the stylesheet sets the size, not the font
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(option)
        width = max(40, rect.width() - 2 * ITEM_PAD_X)
        layout.beginLayout()
        placed, top = [], 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, top))
            top += line.height()
            placed.append(line)
        layout.endLayout()
        local = QPointF(point.x() - rect.x() - ITEM_PAD_X,
                        point.y() - rect.y() - ITEM_PAD_Y)
        for line in placed:
            if line.y() <= local.y() <= line.y() + line.height():
                return word_around(text, line.xToCursor(local.x()))
        return ""

    def _line_menu(self, point: QPoint) -> None:
        item = self.list.itemAt(point)
        if item is None or not item.text().strip():
            return
        word = dictionary.tidy(self._word_at(point))
        menu = QMenu(self)
        if word:
            look = menu.addAction(f"Look up “{word}”")
            look.triggered.connect(
                lambda _checked=False, w=word, p=self.list.viewport().mapToGlobal(point):
                self.look_up(w, p))
        copy = menu.addAction("Copy this line")
        copy.triggered.connect(
            lambda: QApplication.clipboard().setText(item.text().strip()))
        play = menu.addAction("Play from here")
        play.triggered.connect(lambda: self._line_clicked(item))
        menu.exec(self.list.viewport().mapToGlobal(point))

    def look_up(self, word: str, where: QPoint | None = None) -> None:
        """Show what a word means, fetching it if this is the first time."""
        word = dictionary.tidy(word)
        if not word:
            return
        self._looking_up = word
        if where is None:
            where = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 3))
        self._popup.waiting(word)
        self._popup.show_near(where)
        self._pool.start(_LookupJob(word, self._signals))

    def _defined(self, word: str, entry) -> None:
        if word != self._looking_up or not self._popup.isVisible():
            return
        self._popup.show_entry(entry)

    # ----------------------------------------------------------------- actions

    def _follow_toggled(self) -> None:
        self._set_following(self.follow_button.isChecked())
        if self._following:
            self._show(self._wanted_index())
            self.current_row = -1
            self._highlight(self._row_at(self._word_now()))

    def _set_following(self, following: bool, quiet: bool = False) -> None:
        self._following = following
        self.follow_button.setChecked(following)
        self.follow_button.setIcon(
            icon("sync", 14, style.ACCENT if following else style.MUTED))
        if self.text.chapters:
            self._refresh_header()
        if not quiet and not following:
            self.statusChanged.emit("Reading ahead — the text has stopped following")

    def align_here(self) -> None:
        """Say that what is on screen is what is playing, and remember it."""
        if not self.text.chapters:
            return
        self.shift = self.showing - self.audio_chapter
        self.aligned = True
        self._remember(shift=self.shift, aligned=True)
        self._set_following(True, quiet=True)
        self._refresh_header()
        self.statusChanged.emit("Lined the text up with the narration")

    def choose_file(self) -> None:
        start = str(Path(self.book["path"]).parent) if self.book else str(Path.home())
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Which EPUB goes with this book?", start, "EPUB files (*.epub *.EPUB)")
        if not chosen or self.book is None:
            return
        self.aligned = False
        self.anchors = {}
        self._remember(path=chosen, aligned=False, looked=True)
        self._loading_for = self.book["id"]
        self.showing = -1
        self._say("Reading…")
        self._pool.start(_ReadJob(self.book, chosen, self._signals))

    def look_again(self) -> None:
        if self.book is None:
            return
        self._remember(path="", looked=False)
        self.aligned = False
        self.anchors = {}
        self._loading_for = self.book["id"]
        self.showing = -1
        self._say("Looking for the text…")
        self._pool.start(_ReadJob(self.book, "", self._signals))

    def shutdown(self) -> None:
        """Drop whatever is queued; what is already running cannot be hurried."""
        self._settle.stop()
        self._pool.clear()

    def _remember(self, **fields) -> None:
        if self.book is not None:
            self.library.set_book_text(self.book["id"], **fields)

    # ------------------------------------------------------------------- menu

    def _open_menu(self) -> None:
        menu = QMenu(self)
        if self.text.chapters:
            listen = menu.addAction("Find my place by listening")
            listen.triggered.connect(self.find_my_place)
            auto = menu.addAction("Keep it in step automatically")
            auto.setCheckable(True)
            auto.setChecked(self.auto_anchor)
            auto.setToolTip("Listens a little ahead of the narration so the lines\n"
                            "are timed rather than estimated. Costs some CPU.")
            auto.toggled.connect(self._toggle_auto)
            align = menu.addAction("Line the text up here")
            align.triggered.connect(self.align_here)
            find = menu.addAction("Search the book…")
            find.triggered.connect(self._toggle_search)
            menu.addSeparator()
        if self.text.chapters:
            earlier = menu.addAction("Text a touch earlier  (Z)")
            earlier.triggered.connect(lambda: self.nudge_lead(1))
            later = menu.addAction("Text a touch later  (X)")
            later.triggered.connect(lambda: self.nudge_lead(-1))
            menu.addSeparator()
        bigger = menu.addAction("Bigger text")
        bigger.triggered.connect(lambda: self.step_size(1))
        smaller = menu.addAction("Smaller text")
        smaller.triggered.connect(lambda: self.step_size(-1))
        menu.addSeparator()
        choose = menu.addAction("Choose a different EPUB…" if self.text.chapters
                                else "Choose an EPUB…")
        choose.triggered.connect(self.choose_file)
        again = menu.addAction("Look for one again")
        again.triggered.connect(self.look_again)
        if self.text.path:
            menu.addSeparator()
            where = menu.addAction(Path(self.text.path).name)
            where.setEnabled(False)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def _toggle_auto(self, on: bool) -> None:
        self._auto_anchor = bool(on)
        self.library.set("reader_auto_anchor", bool(on))
        self.statusChanged.emit("Keeping the lines timed by listening ahead" if on
                                else "The lines will follow an estimate alone")

    def step_size(self, direction: int) -> None:
        sizes = list(TEXT_SIZES)
        current = min(sizes, key=lambda size: abs(size - self.size_px))
        index = max(0, min(sizes.index(current) + direction, len(sizes) - 1))
        self.library.set("reader_text_size", sizes[index])
        self._apply_text_size()
        self.statusChanged.emit(f"Text at {sizes[index]} px")

    def _apply_text_size(self) -> None:
        # The theme puts a font size on every QWidget, and a stylesheet always
        # beats setFont(), so the reading size has to be a stylesheet too.
        self.list.setStyleSheet(
            f"QListWidget#ReaderText {{ font-size: {self.size_px}px; }}")
        self.list.doItemsLayout()
        if self.current_row >= 0:
            self.list.scrollToItem(self.list.item(self.current_row),
                                   QAbstractItemView.ScrollHint.PositionAtCenter)
