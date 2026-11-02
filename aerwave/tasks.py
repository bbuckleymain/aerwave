"""What Aerwave is doing in the background, in one place.

Scans, lyrics, word timing, subtitles and device syncs all run off the UI
thread, and each used to report only into the status line of whichever page
it belonged to — so a lyrics pass on the Music tab was invisible from the
Videos tab, and nothing at all said a sync had started. Every one of them now
reports here too, and the button at the top right of the window shows the lot:
a turning ring and a count while anything is running, a list of what and how
far on click, and the last few that finished, with how they went.

The centre only keeps a record; it never starts or stops work itself. A task
that can be stopped hands over a function that stops it, and the panel offers
a button for it.
"""

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from . import motion, style
from .icons import icon
from .widgets import ElidedLabel

RECENT = 6                     # finished tasks kept, newest first
SETTLE_MS = 150                # updates arriving faster than this are merged


@dataclass
class Task:
    key: str
    title: str
    detail: str = ""
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.time)
    ended: float = 0.0
    note: str = ""
    failed: bool = False
    cancel: object = None      # a callable that stops it, if it can be stopped
    counted: bool = True       # done/total count things; False: only a percentage

    @property
    def share(self) -> float | None:
        """How far through, 0–1, or None when there is no telling."""
        if self.total > 0:
            return max(0.0, min(self.done / self.total, 1.0))
        return None


class TaskCenter(QObject):
    """The record of background work. Owned by the main window."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running: dict[str, Task] = {}
        self._recent: list[Task] = []
        # Scans report every file; the panel does not need to redraw for each.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self.changed)

    def _touch(self, now: bool = False) -> None:
        if now:
            self._settle.stop()
            self.changed.emit()
        elif not self._settle.isActive():
            self._settle.start()

    def begin(self, key: str, title: str, detail: str = "", cancel=None,
              counted: bool = True) -> Task:
        """Something has started. Starting one that is already running
        refreshes it rather than listing it twice."""
        task = self._running.get(key)
        if task is None:
            task = self._running[key] = Task(key, title, detail, cancel=cancel,
                                             counted=counted)
        else:
            task.title, task.detail = title, detail or task.detail
            task.cancel = cancel or task.cancel
        self._touch(now=True)
        return task

    def update(self, key: str, done: int | None = None, total: int | None = None,
               detail: str | None = None, title: str = "") -> None:
        """Progress. Progress for a task nobody announced starts it, under
        `title`, so a worker that was started from somewhere unexpected still
        shows up."""
        task = self._running.get(key)
        if task is None:
            if not title:
                return
            task = self.begin(key, title)
        if done is not None:
            task.done = done
        if total is not None:
            task.total = total
        if detail is not None:
            task.detail = detail
        self._touch()

    def finish(self, key: str, note: str = "", failed: bool = False) -> None:
        task = self._running.pop(key, None)
        if task is None:
            return
        task.ended = time.time()
        task.note, task.failed, task.cancel = note, failed, None
        self._recent.insert(0, task)
        del self._recent[RECENT:]
        self._touch(now=True)

    def has(self, key: str) -> bool:
        return key in self._running

    def running(self) -> list[Task]:
        return sorted(self._running.values(), key=lambda task: task.started)

    def recent(self) -> list[Task]:
        return list(self._recent)

    def clear_recent(self) -> None:
        self._recent.clear()
        self._touch(now=True)


def _ago(when: float) -> str:
    seconds = max(0, int(time.time() - when))
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min ago"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h ago"
    return "yesterday"


def _running_for(since: float) -> str:
    seconds = max(0, int(time.time() - since))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


# ------------------------------------------------------------------ the panel


class _TaskRow(QWidget):
    """One running task: what, how far, and a way to stop it."""

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 8, 10, 8)
        column.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = ElidedLabel(task.title)
        title.setObjectName("TaskTitle")
        top.addWidget(title, 1)
        share = task.share
        amount = QLabel(f"{int(share * 100)}%" if share is not None
                        else _running_for(task.started))
        amount.setObjectName("TaskMeta")
        top.addWidget(amount)
        if callable(task.cancel):
            stop = QPushButton()
            stop.setObjectName("Flat")
            stop.setIcon(icon("close", 12, style.MUTED))
            stop.setIconSize(QSize(12, 12))
            stop.setFixedSize(22, 22)
            stop.setToolTip("Stop this")
            stop.setCursor(Qt.CursorShape.PointingHandCursor)
            stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            stop.clicked.connect(task.cancel)
            top.addWidget(stop)
        column.addLayout(top)

        bar = QProgressBar()
        bar.setObjectName("TaskBar")
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        if share is None:
            bar.setRange(0, 0)             # turning over, length unknown
        else:
            bar.setRange(0, 1000)
            bar.setValue(int(share * 1000))
        column.addWidget(bar)

        detail = task.detail
        if task.total > 0 and task.counted:
            detail = f"{task.done} of {task.total}" + (f" · {detail}" if detail else "")
        if detail:
            line = ElidedLabel(detail)
            line.setObjectName("TaskMeta")
            column.addWidget(line)


class _DoneRow(QWidget):
    """One finished task: what it was, how it went, and when."""

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 5, 14, 5)
        row.setSpacing(9)
        dot = QLabel("●")
        dot.setObjectName("TaskFailed" if task.failed else "TaskDone")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = ElidedLabel(task.title)
        title.setObjectName("TaskDoneTitle")
        text.addWidget(title)
        if task.note:
            note = ElidedLabel(task.note)
            note.setObjectName("TaskMeta")
            text.addWidget(note)
        row.addLayout(text, 1)
        when = QLabel(_ago(task.ended))
        when.setObjectName("TaskMeta")
        row.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)


class TasksPanel(QFrame):
    """The dropdown. A popup, so a click anywhere else puts it away."""

    WIDTH = 350

    def __init__(self, center: TaskCenter, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.center = center
        self.setObjectName("TasksPanel")
        self.setFixedWidth(self.WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._body = QVBoxLayout(self)
        self._body.setContentsMargins(0, 8, 0, 8)
        self._body.setSpacing(0)
        center.changed.connect(self._rebuild)
        # "2 min ago" and the running clocks move on even when nothing reports.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._rebuild)

    def _heading(self, text: str, action: QPushButton | None = None) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(14, 6, 10, 4)
        label = QLabel(text.upper())
        label.setObjectName("SectionLabel")
        row.addWidget(label, 1)
        if action is not None:
            row.addWidget(action)
        self._body.addLayout(row)

    def _clear(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                layout = item.layout()
                while layout.count():
                    inner = layout.takeAt(0).widget()
                    if inner is not None:
                        inner.deleteLater()
                layout.deleteLater()

    def _rebuild(self) -> None:
        if not self.isVisible():
            return
        self._clear()
        running = self.center.running()
        self._heading("Running" if running else "Background tasks")
        if running:
            for task in running:
                self._body.addWidget(_TaskRow(task, self))
        else:
            quiet = QLabel("Nothing is running in the background.")
            quiet.setObjectName("TaskQuiet")
            quiet.setContentsMargins(14, 4, 14, 8)
            self._body.addWidget(quiet)

        recent = self.center.recent()
        if recent:
            line = QFrame()
            line.setObjectName("TaskRule")
            line.setFixedHeight(1)
            self._body.addSpacing(4)
            self._body.addWidget(line)
            self._body.addSpacing(4)
            clear = QPushButton("Clear")
            clear.setObjectName("TaskClear")
            clear.setCursor(Qt.CursorShape.PointingHandCursor)
            clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            clear.clicked.connect(self.center.clear_recent)
            self._heading("Recent", clear)
            for task in recent:
                self._body.addWidget(_DoneRow(task, self))
        self.adjustSize()

    def open_under(self, anchor: QWidget) -> None:
        """Drop down from `anchor`, right edges lined up, kept on screen."""
        self.show()                    # visible first, so the rebuild runs
        self._rebuild()
        corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
        x = corner.x() - self.width()
        screen = anchor.screen()
        if screen is not None:
            room = screen.availableGeometry()
            x = max(room.left() + 8, min(x, room.right() - self.width() - 8))
        self.move(x, corner.y())
        motion.fade_in_window(self)
        self._clock.start()

    def hideEvent(self, event) -> None:
        self._clock.stop()
        super().hideEvent(event)


# ----------------------------------------------------------------- the button


class TasksButton(QPushButton):
    """Top right of the window: a ring that turns while anything is running,
    with the count inside it, and a quiet tick when all is done."""

    SIZE = 30

    def __init__(self, center: TaskCenter, parent=None):
        super().__init__(parent)
        self.center = center
        self.setObjectName("Flat")
        self.setFixedSize(self.SIZE + 6, self.SIZE + 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._angle = 0.0
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._turn)
        self.panel = TasksPanel(center, self.window())
        self.clicked.connect(self._toggle)
        center.changed.connect(self._refresh)
        self._refresh()

    def _toggle(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.open_under(self)

    def _refresh(self) -> None:
        count = len(self.center.running())
        if count:
            names = ", ".join(task.title for task in self.center.running()[:3])
            self.setToolTip(f"Running: {names}" + ("…" if count > 3 else ""))
            if not self._spin.isActive():
                self._spin.start()
        else:
            self.setToolTip("Background tasks — nothing running")
            self._spin.stop()
        self.update()

    def _turn(self) -> None:
        self._angle = (self._angle + 7.5) % 360
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)           # the hover wash, from the theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        box = QRectF(0, 0, 18, 18)
        box.moveCenter(QRectF(self.rect()).center())
        count = len(self.center.running())
        if count:
            track = QColor(style.ACCENT)
            track.setAlphaF(0.22)
            painter.setPen(QPen(track, 2.2))
            painter.drawEllipse(box)
            pen = QPen(QColor(style.ACCENT), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            # The arc breathes as it turns, the way a system spinner does.
            span = 90 + 70 * math.sin(math.radians(self._angle * 2))
            painter.drawArc(box, int(-self._angle * 16), int(span * 16))
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(style.TEXT))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter,
                             str(count) if count < 10 else "9+")
        else:
            pen = QPen(QColor(style.MUTED), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawEllipse(box.adjusted(1, 1, -1, -1))
            # a small tick: all quiet
            centre = box.center()
            painter.drawPolyline([centre + QPointF(-3.6, 0.2), centre + QPointF(-1.0, 2.8),
                                  centre + QPointF(3.8, -2.4)])
        painter.end()
