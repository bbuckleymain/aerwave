"""The audiobook player: cover, chapter list, bookmarks, sleep timer, speed."""

import bisect
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from . import style
from .audio_engine import AudioEngine
from .book_reader import ReaderPanel
from .book_scanner import read_chapters
from .icons import icon
from .idle import IdleInhibitor
from .widgets import ElidedLabel, SeekBar, Toast, VolumeBar, format_time

SKIP_SECONDS = 30
FINE_SKIP = 10
SPEEDS = [0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0]
SLEEP_MINUTES = [5, 10, 15, 30, 45, 60, 90]
SAVE_EVERY_MS = 5000
FADE_SECONDS = 12          # volume ramp before the sleep timer pauses
FINISH_TAIL = 30.0         # this close to the end counts as finished

CHAPTER_ROLE = Qt.ItemDataRole.UserRole + 1
BOOKMARK_ROLE = Qt.ItemDataRole.UserRole + 2


def smart_rewind(last_played: float) -> float:
    """Step back a little when picking a book up again.

    The longer it has been, the more context you need to get your bearings —
    the same trick every audiobook app uses.
    """
    if not last_played:
        return 0.0
    gap = max(0.0, time.time() - last_played)
    if gap < 60:
        return 0.0
    if gap < 600:
        return 5.0
    if gap < 3600:
        return 10.0
    if gap < 86400:
        return 20.0
    return 30.0


def spoken_length(seconds: float) -> str:
    """'6h 12m' — durations at audiobook scale."""
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours}h {minutes:02d}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _button(name: str, tip: str, size: int = 22, colour: str = style.TEXT) -> QPushButton:
    button = QPushButton()
    button.setObjectName("Flat")
    button.setIcon(icon(name, size, colour))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setFixedSize(size + 16, size + 16)
    return button


class BookPlayerPage(QWidget):
    """Plays one book — which may be one file with chapters, or many files."""

    backRequested = Signal()
    progressChanged = Signal(int)
    helpRequested = Signal()
    seeked = Signal(float)             # position jumped, in seconds

    def __init__(self, library, covers, parent=None):
        super().__init__(parent)
        self.setObjectName("BookPlayerRoot")
        self.library = library
        self.covers = covers

        self.book: dict | None = None
        self.tracks: list[dict] = []
        self.chapters: list[dict] = []
        self.track_index = 0
        self.elapsed = 0.0
        self._counted_play = False
        self._finished = False
        self._sleep_deadline = 0.0
        self._sleep_end_of_chapter = False
        self._sleep_base_volume = 100.0

        self.engine = AudioEngine(self)
        # Optional: listening does not need the screen, but it does need the
        # machine to stay awake if the user asks for that.
        self.inhibitor = IdleInhibitor(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), 1)
        root.addWidget(self._build_transport())

        self.toast = Toast(self, anchor="bottom")

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(SAVE_EVERY_MS)
        self._save_timer.timeout.connect(self._on_save_tick)

        self._sleep_timer = QTimer(self)
        self._sleep_timer.setInterval(1000)
        self._sleep_timer.timeout.connect(self._tick_sleep)

        self._wire_engine()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.engine.set_volume(float(library.get("volume", 100)))
        self.engine.set_muted(bool(library.get("muted", False)))
        self._sync_controls()

    def _sync_controls(self) -> None:
        """Push engine state into the widgets; observers only fire on change."""
        self._on_paused(self.engine.paused)
        self._on_volume(self.engine.volume)
        self._on_muted(self.engine.muted)
        self._on_speed(self.engine.speed)

    # ------------------------------------------------------------------ build

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 8, 14, 8)
        row.setSpacing(10)

        back = _button("back", "Back to the shelf  (Esc)")
        back.clicked.connect(self.backRequested)
        row.addWidget(back)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.title_label = ElidedLabel("")
        self.title_label.setObjectName("PageTitle")
        self.author_label = ElidedLabel("")
        self.author_label.setObjectName("PageSubtitle")
        titles.addWidget(self.title_label)
        titles.addWidget(self.author_label)
        row.addLayout(titles, 1)

        gear = _button("gear", "Preferences", 20)
        gear.clicked.connect(self._open_settings_menu)
        row.addWidget(gear)
        return bar

    def _build_body(self) -> QWidget:
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(26, 22, 26, 14)
        row.setSpacing(26)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addStretch(1)
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(300, 300)
        self.cover_label.setObjectName("CoverArt")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.now_label = QLabel("")
        self.now_label.setObjectName("NowPlaying")
        self.now_label.setWordWrap(True)
        self.now_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.now_label)

        self.remaining_label = QLabel("")
        self.remaining_label.setObjectName("HintLabel")
        self.remaining_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.remaining_label.setWordWrap(True)
        left.addWidget(self.remaining_label)
        left.addStretch(1)
        row.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(10)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.chapters_tab = QPushButton("  Chapters")
        self.chapters_tab.setObjectName("NavItem")
        self.chapters_tab.setIcon(icon("list", 16, style.MUTED))
        self.bookmarks_tab = QPushButton("  Bookmarks")
        self.bookmarks_tab.setObjectName("NavItem")
        self.bookmarks_tab.setIcon(icon("bookmark", 16, style.MUTED))
        self.read_tab = QPushButton("  Read")
        self.read_tab.setObjectName("NavItem")
        self.read_tab.setIcon(icon("book", 16, style.MUTED))
        self.read_tab.setToolTip("The book's own text, in step with the narration  (R)")
        self.tabs = (self.chapters_tab, self.bookmarks_tab, self.read_tab)
        for index, button in enumerate(self.tabs):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, i=index: self._show_panel(i))
            tabs.addWidget(button)
        tabs.addStretch(1)
        self.chapters_tab.setChecked(True)
        right.addLayout(tabs)

        self.panels = QStackedWidget()
        self.chapter_list = QListWidget()
        self.chapter_list.setObjectName("ChapterList")
        self.chapter_list.setFrameShape(QFrame.Shape.NoFrame)
        self.chapter_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.chapter_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.chapter_list.itemActivated.connect(self._on_chapter_activated)
        self.chapter_list.itemClicked.connect(self._on_chapter_activated)
        self.panels.addWidget(self.chapter_list)

        self.bookmark_list = QListWidget()
        self.bookmark_list.setObjectName("ChapterList")
        self.bookmark_list.setFrameShape(QFrame.Shape.NoFrame)
        self.bookmark_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.bookmark_list.itemActivated.connect(self._on_bookmark_activated)
        self.bookmark_list.itemClicked.connect(self._on_bookmark_activated)
        self.bookmark_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmark_list.customContextMenuRequested.connect(self._bookmark_menu)
        self.panels.addWidget(self.bookmark_list)

        self.reader = ReaderPanel(self.library, self)
        self.reader.seekRequested.connect(self.seek_book)
        self.reader.statusChanged.connect(lambda message: self.toast.flash(message, 2600))
        self.panels.addWidget(self.reader)
        right.addWidget(self.panels, 1)
        row.addLayout(right, 1)
        return body

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("ControlBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(20, 10, 20, 14)
        outer.setSpacing(6)

        self.seek_bar = SeekBar()
        self.seek_bar.seeked.connect(self.seek_book)
        self.seek_bar.scrubbing.connect(self._on_scrubbing)
        self.seek_bar.hovered.connect(self._on_seek_hover)
        self.seek_bar.unhovered.connect(lambda: self.hover_time.hide())
        outer.addWidget(self.seek_bar)

        self.hover_time = QLabel(self)
        self.hover_time.setObjectName("Toast")
        self.hover_time.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hover_time.hide()

        row = QHBoxLayout()
        row.setSpacing(3)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("TimeLabel")
        row.addWidget(self.time_label)
        row.addStretch(1)

        self.prev_button = _button("prev", "Previous chapter  (P)", 20)
        self.prev_button.clicked.connect(self.previous_chapter)
        self.back_button = _button(f"back{SKIP_SECONDS}", f"Back {SKIP_SECONDS} seconds  (←)", 24)
        self.back_button.clicked.connect(lambda: self.skip(-SKIP_SECONDS))
        self.play_button = _button("play", "Play / pause  (Space)", 30)
        self.play_button.clicked.connect(self.toggle_pause)
        self.forward_button = _button(f"forward{SKIP_SECONDS}",
                                      f"Forward {SKIP_SECONDS} seconds  (→)", 24)
        self.forward_button.clicked.connect(lambda: self.skip(SKIP_SECONDS))
        self.next_button = _button("next", "Next chapter  (N)", 20)
        self.next_button.clicked.connect(self.next_chapter)
        for widget in (self.prev_button, self.back_button, self.play_button,
                       self.forward_button, self.next_button):
            row.addWidget(widget)
        row.addStretch(1)

        self.speed_button = QPushButton("1×")
        self.speed_button.setObjectName("Flat")
        self.speed_button.setToolTip("Listening speed  ([ and ])")
        self.speed_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speed_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_button.setFixedHeight(34)
        self.speed_button.setMinimumWidth(52)
        self.speed_button.clicked.connect(self._open_speed_menu)
        row.addWidget(self.speed_button)

        self.bookmark_button = _button("bookmark_add", "Bookmark this spot  (B)", 20)
        self.bookmark_button.clicked.connect(self.add_bookmark)
        row.addWidget(self.bookmark_button)

        self.sleep_button = QPushButton()
        self.sleep_button.setObjectName("Flat")
        self.sleep_button.setIcon(icon("moon", 20, style.TEXT))
        self.sleep_button.setIconSize(QSize(20, 20))
        self.sleep_button.setToolTip("Sleep timer  (T)")
        self.sleep_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sleep_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sleep_button.setFixedHeight(36)
        self.sleep_button.clicked.connect(self._open_sleep_menu)
        row.addWidget(self.sleep_button)

        self.mute_button = _button("volume", "Mute  (M)", 19)
        self.mute_button.clicked.connect(self.toggle_mute)
        row.addWidget(self.mute_button)
        self.volume_bar = VolumeBar()
        self.volume_bar.changed.connect(self._on_volume_bar)
        row.addWidget(self.volume_bar)

        outer.addLayout(row)
        return bar

    def _wire_engine(self) -> None:
        self.engine.positionChanged.connect(self._on_position)
        self.engine.durationChanged.connect(self._on_duration)
        self.engine.pausedChanged.connect(self._on_paused)
        self.engine.volumeChanged.connect(self._on_volume)
        self.engine.mutedChanged.connect(self._on_muted)
        self.engine.speedChanged.connect(self._on_speed)
        self.engine.trackEnded.connect(self._on_track_ended)
        self.engine.fileLoaded.connect(self._on_file_loaded)
        self.engine.engineError.connect(lambda message: self.toast.flash(message, 3000))

    # ---------------------------------------------------------------- opening

    def play(self, book: dict, resume: bool = True) -> None:
        self.save_progress(flush=True)
        self.book = book
        self.tracks = self.library.book_tracks(book["id"])
        if not self.tracks:
            self.tracks = [{"idx": 0, "path": book["path"], "title": book["title"],
                            "duration": book.get("duration") or 0, "start": 0.0}]
        self._counted_play = False
        self._finished = False
        self.cancel_sleep_timer(quiet=True)

        self.title_label.setText(book["title"])
        self.author_label.setText(self._byline(book))
        self._set_cover(book)
        self.seek_bar.set_duration(self._total_duration())

        start = float(book.get("elapsed") or 0) if resume else 0.0
        if resume and start > 0:
            rewind = smart_rewind(book.get("last_played") or 0)
            start = max(0.0, start - rewind)
        if book.get("finished") and resume:
            start = 0.0

        self.engine.set_speed(float(book.get("speed") or 1.0))
        self.engine.set_normalisation(bool(self.library.get("level_volume", False)))
        index, offset = self._locate(start)
        self._load_track(index, offset)
        self._refresh_bookmarks()
        self.reader.load_for(book)
        self._save_timer.start()
        self._sync_controls()
        QTimer.singleShot(250, self._sync_controls)
        self._refresh_idle_inhibit()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if start > 30:
            self.toast.flash(f"Resumed at {self.clock(start)}", 2400)

    def clock(self, seconds: float) -> str:
        """A timestamp, with an hours field only when the book is that long."""
        return format_time(seconds, self._total_duration() >= 3600)

    @staticmethod
    def _byline(book: dict) -> str:
        bits = [book.get("author") or ""]
        if book.get("narrator"):
            bits.append(f"read by {book['narrator']}")
        if book.get("series"):
            bits.append(book["series"])
        return "  ·  ".join(b for b in bits if b)

    def _set_cover(self, book: dict) -> None:
        pixmap = self.covers.get(book, online=True)
        self._apply_cover(pixmap)

    def _apply_cover(self, pixmap: QPixmap) -> None:
        size = self.cover_label.width()
        self.cover_label.setPixmap(pixmap.scaled(
            QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def cover_updated(self, book_id: int) -> None:
        if self.book and self.book["id"] == book_id:
            self._set_cover(self.book)

    def stop(self) -> None:
        self.save_progress(flush=True)
        self._save_timer.stop()
        self.cancel_sleep_timer(quiet=True)
        self.engine.stop()
        self.reader.load_for(None)
        self.book = None
        self.inhibitor.release()

    def shutdown_timers(self) -> None:
        self._save_timer.stop()
        self._sleep_timer.stop()
        self.reader.shutdown()
        self.inhibitor.release()

    # ------------------------------------------------------- position mapping

    def _total_duration(self) -> float:
        if self.book and self.book.get("duration"):
            return float(self.book["duration"])
        return sum(float(t["duration"] or 0) for t in self.tracks)

    def _locate(self, elapsed: float) -> tuple[int, float]:
        """Map a position in the whole book to (track index, offset in track)."""
        elapsed = max(0.0, elapsed)
        for index in range(len(self.tracks) - 1, -1, -1):
            track = self.tracks[index]
            start = float(track["start"] or 0)
            if elapsed >= start - 0.001:
                return index, min(elapsed - start, max(0.0, float(track["duration"] or 0)))
        return 0, 0.0

    def _load_track(self, index: int, offset: float = 0.0, paused: bool = False) -> None:
        index = max(0, min(index, len(self.tracks) - 1))
        self.track_index = index
        track = self.tracks[index]
        if not Path(track["path"]).exists():
            self.toast.flash(f"Missing file: {Path(track['path']).name}", 3500)
            return
        self.elapsed = float(track["start"] or 0) + offset
        self.engine.open(track["path"], start_at=offset, paused=paused)
        self._rebuild_chapters()

    # --------------------------------------------------------------- chapters

    def _rebuild_chapters(self) -> None:
        """Chapters are the book's own marks, or its files when it has several."""
        total = self._total_duration()
        chapters: list[dict] = []
        if len(self.tracks) > 1:
            for track in self.tracks:
                start = float(track["start"] or 0)
                chapters.append({"title": track["title"] or f"Part {track['idx'] + 1}",
                                 "start": start,
                                 "end": start + float(track["duration"] or 0)})
        else:
            marks = self.engine.chapters() or read_chapters(self.tracks[0]["path"])
            for position, mark in enumerate(marks):
                end = marks[position + 1]["start"] if position + 1 < len(marks) else total
                chapters.append({"title": mark["title"], "start": mark["start"], "end": end})
        if not chapters:
            chapters = [{"title": self.book["title"] if self.book else "Audiobook",
                         "start": 0.0, "end": total}]
        if chapters == self.chapters:
            return
        self.chapters = chapters
        self.chapter_list.clear()
        for number, chapter in enumerate(chapters, 1):
            length = spoken_length(chapter["end"] - chapter["start"])
            item = QListWidgetItem(f"  {number}.  {chapter['title']}\n      {length}")
            item.setData(CHAPTER_ROLE, chapter["start"])
            self.chapter_list.addItem(item)
        self.seek_bar.set_marks([c["start"] for c in chapters[1:]])
        self.reader.set_chapters(chapters)
        self._highlight_chapter(force=True)

    def chapter_at(self, elapsed: float) -> int:
        """Which chapter a moment falls in. Asked several times a second."""
        if not self.chapters:
            return 0
        # The marks are in order, so there is no need to walk them.
        index = bisect.bisect_right(self.chapters, elapsed + 0.001,
                                    key=lambda mark: mark["start"]) - 1
        return max(0, index)

    def _highlight_chapter(self, force: bool = False) -> None:
        if not self.chapters:
            return
        index = self.chapter_at(self.elapsed)
        if not force and getattr(self, "_shown_chapter", -1) == index:
            return
        self._shown_chapter = index
        self.reader.follow_audio(index)
        for row in range(self.chapter_list.count()):
            item = self.chapter_list.item(row)
            font = QFont(item.font())
            font.setWeight(QFont.Weight.Bold if row == index else QFont.Weight.Normal)
            item.setFont(font)
            item.setForeground(Qt.GlobalColor.white if row == index
                               else Qt.GlobalColor.lightGray)
        self.chapter_list.setCurrentRow(index)
        chapter = self.chapters[index]
        self.now_label.setText(
            f"Chapter {index + 1} of {len(self.chapters)}\n{chapter['title']}"
        )

    # ----------------------------------------------------------- engine hooks

    def _on_file_loaded(self) -> None:
        self._rebuild_chapters()

    def _on_position(self, position: float) -> None:
        if self.seek_bar.dragging:
            return
        track = self.tracks[self.track_index] if self.tracks else {"start": 0}
        self.elapsed = float(track.get("start") or 0) + position
        self.reader.follow_position(self.elapsed, track.get("path", ""), position)
        self.seek_bar.set_position(self.elapsed)
        self._update_labels()
        self._highlight_chapter()
        if self._sleep_end_of_chapter and self.chapters:
            chapter = self.chapters[self.chapter_at(self.elapsed)]
            if self.elapsed >= chapter["end"] - 0.6:
                self._sleep_end_of_chapter = False
                self._sleep_fire()

    def _on_duration(self, duration: float) -> None:
        # A track whose length was unknown at scan time (or changed) — trust mpv.
        if duration > 0 and self.tracks:
            track = self.tracks[self.track_index]
            if abs(float(track["duration"] or 0) - duration) > 1.5 and len(self.tracks) == 1:
                track["duration"] = duration
                if self.book is not None:
                    self.book["duration"] = duration
                self.seek_bar.set_duration(duration)
                self._rebuild_chapters()

    def _on_track_ended(self) -> None:
        if self.book is None:
            return
        if self.track_index + 1 < len(self.tracks):
            self._load_track(self.track_index + 1, 0.0)
            self.toast.flash(self.tracks[self.track_index]["title"], 1600)
            return
        self._finish_book()

    def _finish_book(self) -> None:
        if self.book is None or self._finished:
            return
        self._finished = True
        self.library.set_book_finished(self.book["id"], True)
        self.book["finished"] = 1
        self.progressChanged.emit(self.book["id"])
        self.engine.set_paused(True)
        self.toast.flash("Finished — nicely done", 3000)

    def _on_paused(self, paused: bool) -> None:
        self.play_button.setIcon(icon("play" if paused else "pause", 30, style.TEXT))
        self._refresh_idle_inhibit(paused)
        if paused:
            self.save_progress()

    def _refresh_idle_inhibit(self, paused: bool | None = None) -> None:
        if paused is None:
            paused = self.engine.paused
        wanted = bool(self.library.get("keep_awake_listening", False))
        playing = wanted and self.book is not None and not paused
        reason = f"Listening to {self.book['title']}" if playing and self.book else "Playing audio"
        self.inhibitor.set_active(playing, reason)

    def _on_volume(self, volume: float) -> None:
        self.volume_bar.set_value(volume)
        self._refresh_volume_icon()

    def _on_muted(self, muted: bool) -> None:
        self.volume_bar.set_muted(muted)
        self._refresh_volume_icon()

    def _refresh_volume_icon(self) -> None:
        volume = self.engine.volume
        if self.engine.muted or volume <= 0:
            name = "volume_mute"
        elif volume > 100:
            name = "volume_boost"
        elif volume < 45:
            name = "volume_low"
        else:
            name = "volume"
        self.mute_button.setIcon(icon(name, 19, style.TEXT))

    def _on_speed(self, speed: float) -> None:
        self.speed_button.setText(f"{speed:g}×")
        self._update_labels()

    # ------------------------------------------------------------------- UI

    def _update_labels(self) -> None:
        total = self._total_duration()
        long_form = total >= 3600
        self.time_label.setText(
            f"{format_time(self.elapsed, long_form)} / {format_time(total, long_form)}"
        )
        remaining = max(0.0, total - self.elapsed)
        speed = self.engine.speed or 1.0
        text = f"{spoken_length(remaining)} left"
        if abs(speed - 1.0) > 0.01:
            text += f"  ·  {spoken_length(remaining / speed)} at {speed:g}×"
        if self._sleep_deadline:
            text += f"  ·  sleeping in {format_time(self._sleep_deadline - time.time())}"
        elif self._sleep_end_of_chapter:
            text += "  ·  sleeping at end of chapter"
        self.remaining_label.setText(text)

    def _show_panel(self, index: int) -> None:
        self.panels.setCurrentIndex(index)
        for position, button in enumerate(self.tabs):
            button.setChecked(position == index)

    def _on_scrubbing(self, elapsed: float) -> None:
        self.elapsed = elapsed
        self._update_labels()

    def _on_seek_hover(self, seconds: float, x: int) -> None:
        label = self.clock(seconds)
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
        self.engine.set_volume(value)
        if value > 0 and self.engine.muted:
            self.engine.set_muted(False)
        self.library.set("volume", round(value, 1))
        self._sleep_base_volume = value

    # -------------------------------------------------------------- transport

    def toggle_pause(self) -> None:
        self.engine.toggle_pause()

    def skip(self, seconds: float) -> None:
        self.seek_book(self.elapsed + seconds)
        self.toast.flash(f"{'+' if seconds > 0 else '−'}{abs(int(seconds))}s", 700)

    def seek_book(self, elapsed: float) -> None:
        total = self._total_duration()
        elapsed = max(0.0, min(elapsed, max(0.0, total - 0.5)))
        index, offset = self._locate(elapsed)
        if index != self.track_index:
            self._load_track(index, offset, paused=self.engine.paused)
        else:
            self.engine.seek_absolute(offset)
        self.elapsed = elapsed
        self.seek_bar.set_position(elapsed)
        self._update_labels()
        self._highlight_chapter()
        self.seeked.emit(elapsed)

    def next_chapter(self) -> None:
        index = self.chapter_at(self.elapsed) + 1
        if index >= len(self.chapters):
            self.toast.flash("Last chapter")
            return
        self.seek_book(self.chapters[index]["start"])
        self.toast.flash(self.chapters[index]["title"], 1400)

    def previous_chapter(self) -> None:
        index = self.chapter_at(self.elapsed)
        start = self.chapters[index]["start"] if self.chapters else 0.0
        # Like every other player: restart this chapter unless you just began it.
        if self.elapsed - start > 3 or index == 0:
            self.seek_book(start)
        else:
            self.seek_book(self.chapters[index - 1]["start"])
        self.toast.flash(self.chapters[self.chapter_at(self.elapsed)]["title"], 1400)

    def adjust_volume(self, delta: float) -> None:
        value = self.engine.adjust_volume(delta)
        self._sleep_base_volume = value
        self.library.set("volume", round(value, 1))
        self.toast.flash(f"Volume {int(round(value))}%", 900)

    def toggle_mute(self) -> None:
        self.engine.toggle_mute()
        self.library.set("muted", self.engine.muted)

    def set_speed(self, speed: float) -> None:
        self.engine.set_speed(speed)
        if self.book is not None:
            self.library.save_book_progress(self.book["id"], self.elapsed, speed=speed)
            self.book["speed"] = speed
        self.toast.flash(f"{speed:g}× speed", 900)

    def nudge_speed(self, direction: int) -> None:
        current = self.engine.speed
        if direction > 0:
            target = next((s for s in SPEEDS if s > current + 1e-3), SPEEDS[-1])
        else:
            target = next((s for s in reversed(SPEEDS) if s < current - 1e-3), SPEEDS[0])
        self.set_speed(target)

    # -------------------------------------------------------------- bookmarks

    def add_bookmark(self) -> None:
        if self.book is None:
            return
        chapter = self.chapters[self.chapter_at(self.elapsed)]["title"] if self.chapters else ""
        self.library.add_bookmark(self.book["id"], self.elapsed, chapter)
        self._refresh_bookmarks()
        self._show_panel(1)
        self.toast.flash(f"Bookmarked {self.clock(self.elapsed)}")

    def _refresh_bookmarks(self) -> None:
        self.bookmark_list.clear()
        if self.book is None:
            return
        marks = self.library.bookmarks(self.book["id"])
        text = f"  Bookmarks{f'  ({len(marks)})' if marks else ''}"
        self.bookmarks_tab.setText(text)
        self.bookmarks_tab.setMinimumWidth(
            self.bookmarks_tab.fontMetrics().horizontalAdvance(text) + 46)
        for mark in marks:
            label = mark["label"] or "Bookmark"
            item = QListWidgetItem(f"  {self.clock(mark['elapsed'])}\n      {label}")
            item.setData(BOOKMARK_ROLE, mark)
            self.bookmark_list.addItem(item)
        if not marks:
            placeholder = QListWidgetItem("  No bookmarks yet — press B to drop one.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.bookmark_list.addItem(placeholder)

    def _on_bookmark_activated(self, item: QListWidgetItem) -> None:
        mark = item.data(BOOKMARK_ROLE)
        if mark:
            self.seek_book(float(mark["elapsed"]))

    def _bookmark_menu(self, position: QPoint) -> None:
        item = self.bookmark_list.itemAt(position)
        mark = item.data(BOOKMARK_ROLE) if item else None
        if not mark:
            return
        menu = QMenu(self)
        jump = menu.addAction(f"Jump to {self.clock(mark['elapsed'])}")
        jump.triggered.connect(lambda: self.seek_book(float(mark["elapsed"])))
        rename = menu.addAction("Rename…")
        rename.triggered.connect(lambda: self._rename_bookmark(mark))
        menu.addSeparator()
        delete = menu.addAction("Delete")
        delete.triggered.connect(lambda: (self.library.remove_bookmark(mark["id"]),
                                          self._refresh_bookmarks()))
        menu.exec(self.bookmark_list.viewport().mapToGlobal(position))

    def _rename_bookmark(self, mark: dict) -> None:
        label, ok = QInputDialog.getText(self, "Rename bookmark", "Note:", text=mark["label"])
        if ok:
            self.library.rename_bookmark(mark["id"], label.strip())
            self._refresh_bookmarks()

    def _on_chapter_activated(self, item: QListWidgetItem) -> None:
        start = item.data(CHAPTER_ROLE)
        if start is not None:
            self.seek_book(float(start))

    # ------------------------------------------------------------ sleep timer

    def _open_sleep_menu(self) -> None:
        menu = QMenu(self)
        if self._sleep_deadline or self._sleep_end_of_chapter:
            off = menu.addAction("Cancel sleep timer")
            off.triggered.connect(self.cancel_sleep_timer)
            menu.addSeparator()
        for minutes in SLEEP_MINUTES:
            action = menu.addAction(f"{minutes} minutes")
            action.triggered.connect(lambda _checked=False, m=minutes: self.start_sleep_timer(m))
        menu.addSeparator()
        chapter = menu.addAction("At the end of this chapter")
        chapter.triggered.connect(self.sleep_at_chapter_end)
        menu.exec(self.sleep_button.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def start_sleep_timer(self, minutes: int) -> None:
        self._sleep_end_of_chapter = False
        self._sleep_deadline = time.time() + minutes * 60
        self._sleep_base_volume = self.engine.volume
        self._sleep_timer.start()
        self.sleep_button.setIcon(icon("moon", 20, style.ACCENT))
        self.toast.flash(f"Sleeping in {minutes} minutes")
        self._tick_sleep()   # show the countdown now, not a second from now

    def sleep_at_chapter_end(self) -> None:
        self._sleep_deadline = 0.0
        self._sleep_end_of_chapter = True
        self._sleep_base_volume = self.engine.volume
        self._sleep_timer.start()
        self.sleep_button.setIcon(icon("moon", 20, style.ACCENT))
        self.toast.flash("Sleeping at the end of this chapter")
        self._tick_sleep()

    def cancel_sleep_timer(self, quiet: bool = False) -> None:
        had_timer = bool(self._sleep_deadline or self._sleep_end_of_chapter)
        self._sleep_deadline = 0.0
        self._sleep_end_of_chapter = False
        self._sleep_timer.stop()
        self.sleep_button.setIcon(icon("moon", 20, style.TEXT))
        self.sleep_button.setText("")
        if self._sleep_base_volume:
            self.engine.set_volume(self._sleep_base_volume)
        if had_timer and not quiet:
            self.toast.flash("Sleep timer cancelled")
        self._update_labels()

    def _tick_sleep(self) -> None:
        if self._sleep_end_of_chapter:
            self._update_labels()
            return
        if not self._sleep_deadline:
            return
        remaining = self._sleep_deadline - time.time()
        if remaining <= 0:
            self._sleep_fire()
            return
        self.sleep_button.setText(f" {format_time(remaining)}")
        # Ease the volume down over the last few seconds instead of cutting out.
        if remaining <= FADE_SECONDS:
            self.engine.set_volume(self._sleep_base_volume * (remaining / FADE_SECONDS))
        self._update_labels()

    def _sleep_fire(self) -> None:
        self.engine.set_paused(True)
        self.save_progress(flush=True)
        self.cancel_sleep_timer(quiet=True)
        self.toast.flash("Sleep timer — paused. Sweet dreams.", 4000)

    # --------------------------------------------------------------- progress

    def _on_save_tick(self) -> None:
        self.save_progress()
        self._refresh_idle_inhibit()

    def save_progress(self, flush: bool = False) -> None:
        if self.book is None:
            return
        total = self._total_duration()
        elapsed = self.elapsed
        finished = None
        if total and elapsed >= total - FINISH_TAIL:
            finished = True
            elapsed = 0.0
        if elapsed <= 1 and finished is None and not flush:
            return
        bump = False
        if not self._counted_play and self.elapsed > 60:
            self._counted_play = bump = True
        self.library.save_book_progress(self.book["id"], elapsed, finished=finished,
                                        speed=self.engine.speed, bump_count=bump)
        self.progressChanged.emit(self.book["id"])

    def toggle_finished(self) -> None:
        if self.book is None:
            return
        new_state = not bool(self.book.get("finished"))
        self.library.set_book_finished(self.book["id"], new_state)
        self.book["finished"] = 1 if new_state else 0
        self.progressChanged.emit(self.book["id"])
        self.toast.flash("Marked as finished" if new_state else "Marked as unfinished")

    # ----------------------------------------------------------------- menus

    def _open_speed_menu(self) -> None:
        menu = QMenu(self)
        current = round(self.engine.speed, 3)
        for speed in SPEEDS:
            action = menu.addAction(f"{speed:g}×")
            action.setCheckable(True)
            action.setChecked(abs(speed - current) < 1e-3)
            action.triggered.connect(lambda _checked=False, s=speed: self.set_speed(s))
        menu.addSeparator()
        note = menu.addAction("Speed is remembered per book")
        note.setEnabled(False)
        menu.exec(self.speed_button.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def _open_settings_menu(self) -> None:
        menu = QMenu(self)
        level = menu.addAction("Even out quiet narration")
        level.setCheckable(True)
        level.setChecked(bool(self.library.get("level_volume", False)))
        level.toggled.connect(self._toggle_normalisation)
        awake = menu.addAction("Keep the computer awake while listening")
        awake.setCheckable(True)
        awake.setChecked(bool(self.library.get("keep_awake_listening", False)))
        awake.toggled.connect(self._toggle_keep_awake)
        menu.addSeparator()
        cover = menu.addAction("Find cover art…")
        cover.setEnabled(self.book is not None)
        cover.triggered.connect(self.choose_cover)
        finished = menu.addAction("Mark as finished  (F)")
        finished.triggered.connect(self.toggle_finished)
        restart = menu.addAction("Start again from the beginning")
        restart.triggered.connect(lambda: self.seek_book(0))
        menu.addSeparator()
        shortcuts = menu.addAction("Keyboard shortcuts  (?)")
        shortcuts.triggered.connect(self.helpRequested)
        menu.exec(self.mapToGlobal(QPoint(self.width() - 240, 44)))

    def choose_cover(self) -> None:
        """Pick a cover for the book being listened to."""
        if self.book is None:
            return
        from .cover_view import CoverDialog        # noqa: PLC0415  (network, when asked)
        current = self.covers.get(self.book) if self.covers.has_artwork(self.book) else None
        dialog = CoverDialog(self.book, current, self)
        dialog.chosen.connect(lambda source: self.covers.adopt(self.book, source))
        dialog.exec()

    def _toggle_keep_awake(self, enabled: bool) -> None:
        self.library.set("keep_awake_listening", enabled)
        self._refresh_idle_inhibit()
        self.toast.flash("The computer will stay awake while listening" if enabled
                         else "The computer may sleep while listening")

    def _toggle_normalisation(self, enabled: bool) -> None:
        self.library.set("level_volume", enabled)
        self.engine.set_normalisation(enabled)
        self.toast.flash("Volume levelling on" if enabled else "Volume levelling off")

    # ------------------------------------------------------------- shortcuts

    def keyPressEvent(self, event) -> None:
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Space, Qt.Key.Key_K):
            self.toggle_pause()
        elif key == Qt.Key.Key_Left:
            self.skip(-300 if shift else -SKIP_SECONDS)
        elif key == Qt.Key.Key_Right:
            self.skip(300 if shift else SKIP_SECONDS)
        elif key == Qt.Key.Key_J:
            self.skip(-FINE_SKIP)
        elif key == Qt.Key.Key_L:
            self.skip(FINE_SKIP)
        elif key == Qt.Key.Key_Up:
            self.adjust_volume(5)
        elif key == Qt.Key.Key_Down:
            self.adjust_volume(-5)
        elif key == Qt.Key.Key_M:
            self.toggle_mute()
        elif key == Qt.Key.Key_N:
            self.next_chapter()
        elif key == Qt.Key.Key_P:
            self.previous_chapter()
        elif key == Qt.Key.Key_B:
            self.add_bookmark()
        elif key == Qt.Key.Key_T:
            self._open_sleep_menu()
        elif key == Qt.Key.Key_R:
            self._show_panel(0 if self.panels.currentIndex() == 2 else 2)
        elif key in (Qt.Key.Key_Z, Qt.Key.Key_X) and self.reader.text.chapters:
            self.reader.nudge_lead(1 if key == Qt.Key.Key_Z else -1)
        elif key == Qt.Key.Key_Slash:
            self._show_panel(2)
            self.reader.open_search()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            self._show_panel(2)
            self.reader.step_size(-1 if key == Qt.Key.Key_Minus else 1)
        elif key in (Qt.Key.Key_Comma, Qt.Key.Key_Period):
            self._show_panel(2)
            self.reader.turn(-1 if key == Qt.Key.Key_Comma else 1)
        elif key == Qt.Key.Key_F:
            self.toggle_finished()
        elif key == Qt.Key.Key_BracketLeft:
            self.nudge_speed(-1)
        elif key == Qt.Key.Key_BracketRight:
            self.nudge_speed(1)
        elif key == Qt.Key.Key_Backslash:
            self.set_speed(1.0)
        elif key == Qt.Key.Key_Home:
            self.seek_book(0)
        elif key == Qt.Key.Key_End:
            self.seek_book(max(0.0, self._total_duration() - 5))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self.seek_book(self._total_duration() * (key - Qt.Key.Key_0) / 10)
        elif key == Qt.Key.Key_Escape:
            self.backRequested.emit()
        elif key in (Qt.Key.Key_Question, Qt.Key.Key_F1):
            self.helpRequested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()
