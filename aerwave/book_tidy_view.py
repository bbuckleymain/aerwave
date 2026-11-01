"""The dialog that puts an audiobook shelf in order.

Planning means a catalogue lookup for every book, so it happens on a worker
thread and the window fills in as answers arrive. Nothing happens until you
have read the list and said so — a catalogue can be confidently wrong about an
obscure book, and only you can tell.

Renaming files and writing tags are asked for separately, and separately from
the names Aerwave shows, because they are the only parts a rescan cannot put
back. The ones that can be put back are put back by the Undo button, which
walks the journal of everything the last run did.
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from . import book_tidy, style
from .icons import icon

CHANGE_ROLE = Qt.ItemDataRole.UserRole + 1


def _count(number: int, thing: str) -> str:
    return f"{number} {thing}{'' if number == 1 else 's'}"


class _Signals(QObject):
    planned = Signal(int, object)      # how many are done, the change just worked out
    finished = Signal(list)
    applied = Signal(object)


class _PlanJob(QRunnable):
    """Work out what every book needs, one lookup at a time.

    Both the renaming and the retagging are worked out whether or not they are
    wanted, so that ticking either box shows its consequences at once instead
    of asking the catalogue all over again.
    """

    def __init__(self, library, books: list, signals: _Signals):
        super().__init__()
        self.library, self.books, self.signals = library, list(books), signals
        self.cancelled = False
        self.setAutoDelete(True)

    def run(self) -> None:
        changes = []
        for number, book in enumerate(self.books, 1):
            if self.cancelled:
                return
            change = book_tidy.plan_one(self.library, book, rename=True, retag=True)
            changes.append(change)
            try:
                self.signals.planned.emit(number, change)
            except RuntimeError:
                return                 # the dialog closed while we looked
        try:
            self.signals.finished.emit(changes)
        except RuntimeError:
            pass


class _ApplyJob(QRunnable):
    """Carry the changes out, off the interface thread: files can be slow."""

    def __init__(self, library, changes: list, signals: _Signals,
                 *, rename: bool, retag: bool, busy: tuple):
        super().__init__()
        self.library, self.changes, self.signals = library, list(changes), signals
        self.rename, self.retag, self.busy = rename, retag, busy
        self.setAutoDelete(True)

    def run(self) -> None:
        report = book_tidy.apply(self.library, self.changes, rename=self.rename,
                                 retag=self.retag, busy=self.busy)
        try:
            self.signals.applied.emit(report)
        except RuntimeError:
            pass


class _UndoJob(QRunnable):
    def __init__(self, library, batch: int, signals: _Signals):
        super().__init__()
        self.library, self.batch, self.signals = library, batch, signals
        self.setAutoDelete(True)

    def run(self) -> None:
        report = book_tidy.undo(self.library, self.batch)
        try:
            self.signals.applied.emit(report)
        except RuntimeError:
            pass


class _QuietJob(QRunnable):
    def __init__(self, library, books: list, signals: _Signals):
        super().__init__()
        self.library, self.books, self.signals = library, list(books), signals
        self.setAutoDelete(True)

    def run(self) -> None:
        changes = []
        for book in self.books:
            change = book_tidy.plan_one(self.library, book)
            if change.fields:
                book_tidy.apply(self.library, [change])
            changes.append(change)
        try:
            self.signals.finished.emit(changes)
        except RuntimeError:
            pass


class QuietTidy(QObject):
    """Names set right on newly added books, in the background.

    Only the library is touched — never the files — because this runs without
    anyone watching. Anything that changes a file waits for the dialog, where
    it can be read first.
    """

    done = Signal(int)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self._signals = _Signals(self)
        self._signals.finished.connect(self._finished)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._busy = False

    def start(self, limit: int = 40) -> None:
        if self._busy or not self.library.get("tidy_new_books", False):
            return
        seen = set(self.library.get("tidied_books", []) or [])
        fresh = [dict(book) for book in self.library.books() if book["id"] not in seen]
        if not fresh:
            return
        self._busy = True
        self._pool.start(_QuietJob(self.library, fresh[:limit], self._signals))

    def _finished(self, changes: list) -> None:
        self._busy = False
        seen = list(self.library.get("tidied_books", []) or [])
        seen.extend(change.book_id for change in changes)
        self.library.set("tidied_books", seen[-5000:])
        self.done.emit(sum(1 for change in changes if change.fields))

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)


class TidyDialog(QDialog):
    """What the shelf would become, and the chance to say no to any of it."""

    applied = Signal()

    def __init__(self, library, parent=None, busy: tuple = ()):
        super().__init__(parent)
        self.library = library
        self.busy = tuple(busy)
        self.setWindowTitle("Tidy up the audiobooks")
        self.setMinimumSize(780, 600)
        self._changes: list = []
        self._total = 0
        self._working = False

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(11)

        heading = QLabel("Set the names right")
        heading.setObjectName("EmptyTitle")
        column.addWidget(heading)
        blurb = QLabel(
            "Aerwave reads each book's name off the shelf, looks it up for the author and "
            "the series, and shows what it would change. Untick anything that looks "
            "wrong — a catalogue can be sure of itself and still be wrong about an "
            "obscure book.")
        blurb.setObjectName("EmptyBody")
        blurb.setWordWrap(True)
        column.addWidget(blurb)

        self.rename_box = QCheckBox(
            "Also rename the files and put them in  Author / Series / 04 - Title")
        self.rename_box.setToolTip(
            "Moves the files on disk. Books in a series share the series folder.\n"
            "Everything Aerwave moves is written down and can be undone.")
        self.retag_box = QCheckBox("Also write the names into the files' own tags")
        self.retag_box.setToolTip(
            "Changes the files, so other players agree with Aerwave about them.")
        for box, key in ((self.rename_box, "tidy_rename"), (self.retag_box, "tidy_retag")):
            box.setChecked(bool(self.library.get(key, False)))
            box.toggled.connect(self._redraw)
            column.addWidget(box)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        column.addWidget(self.progress)

        self.list = QListWidget()
        self.list.setObjectName("ChapterList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setWordWrap(True)
        column.addWidget(self.list, 1)
        self.nothing = self._build_nothing()
        self.nothing.hide()
        column.addWidget(self.nothing, 1)

        self.note = QLabel("")
        self.note.setObjectName("HintLabel")
        self.note.setWordWrap(True)
        column.addWidget(self.note)

        buttons = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("HintLabel")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        self.undo_button = QPushButton("Undo the last tidy")
        self.undo_button.clicked.connect(self._undo)
        buttons.addWidget(self.undo_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        column.addLayout(buttons)

        self._signals = _Signals(self)
        self._signals.planned.connect(self._one_planned)
        self._signals.finished.connect(self._all_planned)
        self._signals.applied.connect(self._done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._job: _PlanJob | None = None
        self.note.setText(self._note_text())
        self._show_undo()
        self.start()

    def _build_nothing(self) -> QWidget:
        """What a shelf already in order looks like: not an empty box."""
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.setSpacing(10)
        art = QLabel()
        art.setPixmap(icon("check", 56, style.GOOD).pixmap(56, 56))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(art)
        title = QLabel("Everything is already in order")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        body = QLabel("Every book is named the way Aerwave would name it.")
        body.setObjectName("EmptyBody")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        column.addWidget(body)
        return panel

    # ------------------------------------------------------------------ plan

    @property
    def rename(self) -> bool:
        return self.rename_box.isChecked()

    @property
    def retag(self) -> bool:
        return self.retag_box.isChecked()

    def start(self) -> None:
        self.nothing.hide()
        self.list.show()
        books = [dict(book) for book in self.library.books()]
        self._total = len(books)
        self._changes = []
        self.progress.setRange(0, max(1, self._total))
        self.progress.setFormat("Looking up %v of %m")
        self.progress.setValue(0)
        self.list.clear()
        self._job = _PlanJob(self.library, books, self._signals)
        self._pool.start(self._job)

    def _wanted(self, change) -> bool:
        return bool(change.fields) or (self.rename and change.moves) \
            or (self.retag and bool(change.tags))

    def _one_planned(self, done: int, change) -> None:
        self.progress.setValue(done)
        self._changes.append(change)
        self._add(change)

    def _add(self, change) -> None:
        if not self._wanted(change):
            return
        lines = change.lines(rename=self.rename, retag=self.retag)
        item = QListWidgetItem(f"  {change.title}\n      " + "\n      ".join(lines))
        item.setData(CHANGE_ROLE, change)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self.list.addItem(item)

    def _note_text(self) -> str:
        """What ticking the boxes actually means, said plainly."""
        if self.rename and self.retag:
            return ("Your files will be moved and their tags rewritten. "
                    "Everything is written down, and Undo puts it back.")
        if self.rename:
            return ("Your files will be moved on disk. Everything is written down, "
                    "and Undo puts it back.")
        if self.retag:
            return ("The names will be written into your files. Everything is "
                    "written down, and Undo puts it back.")
        return "Only what Aerwave shows changes — your files are left exactly as they are."

    def _redraw(self) -> None:
        """Show the consequences of a box being ticked, without asking again."""
        self.library.set("tidy_rename", self.rename)
        self.library.set("tidy_retag", self.retag)
        self.note.setText(self._note_text())
        self.list.clear()
        for change in self._changes:
            self._add(change)
        self._settle()

    def _all_planned(self, changes: list) -> None:
        self._changes = changes
        self.progress.setValue(self._total)
        self._redraw()

    def _settle(self) -> None:
        wanted = [change for change in self._changes if self._wanted(change)]
        self.progress.setFormat(f"{len(wanted)} of {self._total} need something")
        self.apply_button.setEnabled(bool(wanted) and not self._working)
        if not wanted:
            self.status.setText("")
            self.list.hide()
            self.nothing.show()
        else:
            self.status.setText("")
            self.nothing.hide()
            self.list.show()

    # ----------------------------------------------------------------- apply

    def _chosen(self) -> list:
        chosen = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                change = item.data(CHANGE_ROLE)
                if change is not None:
                    chosen.append(change)
        return chosen

    def _apply(self) -> None:
        chosen = self._chosen()
        if not chosen:
            self.status.setText("Nothing is ticked.")
            return
        if (self.rename or self.retag) and not self._confirm(chosen):
            return
        self._busy(True)
        self.status.setText("Working…")
        self._pool.start(_ApplyJob(self.library, chosen, self._signals,
                                   rename=self.rename, retag=self.retag, busy=self.busy))

    def _confirm(self, chosen: list) -> bool:
        """The last word before anything on disk changes."""
        moving = [change for change in chosen if self.rename and change.moves]
        tagging = [change for change in chosen if self.retag and change.tags]
        if not moving and not tagging:
            return True
        doing = []
        if moving:
            doing.append(f"move {_count(len(moving), 'book')} on disk")
        if tagging:
            doing.append(f"rewrite the tags of {_count(len(tagging), 'book')}")
        example = ""
        if moving:
            first = moving[0]
            example = f"\n\nFor example:\n{first.path}\n→  {first.target}"
        return QMessageBox.question(
            self, "Change the files?",
            "Aerwave is about to " + " and ".join(doing) + "." + example
            + "\n\nEverything it does is written down, and Undo puts it back.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok

    def _undo(self) -> None:
        last = self.library.last_tidy()
        if not last:
            return
        if QMessageBox.question(
                self, "Undo the last tidy",
                f"Put back the {_count(last['changes'], 'change')} "
                f"of the last run?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        self._busy(True)
        self.status.setText("Putting it back…")
        self._pool.start(_UndoJob(self.library, int(last["batch"]), self._signals))

    def _done(self, report) -> None:
        self._busy(False)
        text = report.summary()
        if report.failed:
            text += " — " + "; ".join(report.failed[:3])
            if len(report.failed) > 3:
                text += f" (and {len(report.failed) - 3} more)"
        self.status.setText(text)
        self._show_undo()
        self.applied.emit()
        self.start()                   # plan again, so the list shows where we are now

    def _busy(self, working: bool) -> None:
        self._working = working
        for button in (self.apply_button, self.undo_button, self.rename_box,
                       self.retag_box):
            button.setEnabled(not working)
        if not working:
            self._show_undo()

    def _show_undo(self) -> None:
        last = self.library.last_tidy()
        self.undo_button.setEnabled(bool(last) and not self._working)
        self.undo_button.setToolTip(
            f"Put back the {_count(last['changes'], 'change')} the last run made" if last
            else "Nothing has been changed on disk yet")

    def reject(self) -> None:
        if self._working:
            return                     # files are moving; let it finish
        if self._job is not None:
            self._job.cancelled = True
        self._pool.clear()
        super().reject()
