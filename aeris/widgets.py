"""Custom painted controls: the scrub bar, the volume bar and the on-screen toast."""

import math
import time

from PySide6.QtCore import (QEvent, QPointF, QPropertyAnimation, QRectF, QTimer,
                            Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPen,
                           QPixmap, QTextLayout, QTextOption)
from PySide6.QtWidgets import (QApplication, QGraphicsBlurEffect,
                               QGraphicsOpacityEffect, QGraphicsPixmapItem,
                               QGraphicsScene, QLabel, QLineEdit, QPlainTextEdit,
                               QSizePolicy, QStyledItemDelegate, QTextEdit,
                               QWidget)

from . import style

LINE_SPACING = 1.18      # the spacing good lyric views use
LINE_GAP = 10            # and the air between one line and the next


def typing_now() -> bool:
    """Is the person in the middle of typing something?

    Single-letter shortcuts and text fields want the same keys. A shortcut
    that asks this first leaves the letter to whatever is being typed into,
    which is why F in the search box searches for an f.
    """
    focused = QApplication.focusWidget()
    return isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit))


class ElidedLabel(QLabel):
    """A label that gives way rather than pushing everything else aside.

    A plain QLabel asks a layout for as much width as its text needs, and a
    layout has to oblige — so a window whose minimum width includes one grows
    the moment a long title arrives. That is why clicking a song with a long
    name used to resize the window. This one asks for nothing in particular
    and cuts its text to whatever width it is given, with the whole of it in
    the tooltip when there was not room.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.updateGeometry()
        self._fit()

    def text(self) -> str:
        return self._full

    def sizeHint(self):
        """What it would like: the whole text, as a plain label would ask."""
        hint = super().sizeHint()
        hint.setWidth(QFontMetrics(self.font()).horizontalAdvance(self._full) + 2)
        return hint

    def minimumSizeHint(self):
        """What it insists on: nothing.

        This is the whole point. A layout may give it as little as it likes,
        so a long name can never raise the window's own minimum width and
        force it wider — while the hint above still asks for enough room to
        read, and gets it whenever there is any.
        """
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        room = max(0, self.width() - 1)
        shown = QFontMetrics(self.font()).elidedText(
            self._full, Qt.TextElideMode.ElideRight, room) if room else ""
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class SungLineDelegate(QStyledItemDelegate):
    """Lyrics drawn the way the good lyric views draw them.

    The shape of it is borrowed from Spicy Lyrics, and the idea is that only
    the line being sung is really there: it is bright, a little larger, and
    lit from behind, while everything else sits back at about half strength
    and fades further the further from it you look. A line that has been sung
    dims again rather than staying lit, so the eye is never in two places.

    Where the source timed the words, the bright part of the active line grows
    word by word instead of arriving all at once.
    """

    ACTIVE = 1.0                   # how present each kind of line is
    COMING = 0.51
    GONE = 0.497
    FADE = 0.13                    # lost per line of distance from the active one
    DIMMEST = 0.14
    GROW = 1.05                    # the active line, against the rest
    PLAIN = 0.92                   # untimed lyrics: nothing to follow, all to read

    # The instrumental dots. They are done a little before the singing comes
    # back (REST_DONE), each one rises over REST_RISE of that, and they start
    # REST_STEP apart — overlapping, so the row grows as one movement rather
    # than one dot at a time.
    REST_DONE = 0.92
    REST_STEP = 0.26
    REST_RISE = 0.48
    REST_FRAME = 12                # ms between frames while a break is running
    REST_COAST = 0.25              # how far to carry the clock past a report

    # Depth of field. The line being sung is sharp and everything else is
    # softened by how far from it it is, which is what makes the eye land on
    # the right line without being told. `blur` scales the whole effect: the
    # side panel leaves it off, the stage turns it up.
    BLUR_STEP = 0.95               # px of softening per line of distance
    BLUR_MOST = 3.4                # and never softer than this
    BLUR_BEHIND = 0.6              # lines already sung blur less than those to come

    def __init__(self, line_role: int, parent=None, centred: bool = False,
                 padding: int = 0):
        super().__init__(parent)
        self.line_role = line_role
        self.centred = centred
        self.padding = padding
        self.row = -1                  # which line is being sung
        self.position = 0.0
        self.enabled = True            # word timings may be turned off
        self.ink = QColor(style.TEXT)  # one colour; the states are its alpha
        self.glow = QColor(255, 255, 255, 40)
        self.blur = 0.0                # how much depth of field, 0 for none
        # Lyrics with no timings have no line being sung, and everything
        # used to be drawn as a line still to come — half strength, which on
        # a whole sheet of words is just grey. Plain text is drawn to be read.
        self.plain = False
        self._soft: dict = {}          # blurred lines, kept between frames
        self._reported = time.monotonic()
        self._resting = False          # a break is on screen and running
        # Painting an active break asks for the next frame; once the break is
        # over nothing asks again and the timer falls quiet by itself.
        self._frames = QTimer(self)
        self._frames.setSingleShot(True)
        self._frames.setInterval(self.REST_FRAME)
        self._frames.timeout.connect(self._next_frame)
        # The line being sung used to be drawn 5% larger. A line that only
        # just fitted then wrapped when it lit up and ran into the next one —
        # or, measured at that size, every near-full line kept room for a
        # second row it never used. Brightness and the glow mark it already,
        # which is how the lyric views this follows do it.
        self.grow = False

    def _next_frame(self) -> None:
        view = self.parent()
        if view is not None and hasattr(view, "viewport"):
            view.viewport().update()

    def start_over(self, synced: bool) -> None:
        """A new set of lines: nothing sung yet, and none of the last song's
        place carried over — a stale row left one line lit and the rest
        dimmed and blurred around it."""
        self.row, self.position = -1, 0.0
        self.plain = not synced
        self._soft.clear()
        self._resting = False

    def follow(self, row: int, position: float) -> None:
        self.row, self.position = row, position
        self._reported = time.monotonic()
        if self._resting and not self._frames.isActive():
            self._frames.start()       # playing again after a pause

    def _running(self) -> float:
        """The position, carried forward since it was last reported.

        Positions arrive about fifteen times a second, which is plenty for
        words but visibly steppy for something that grows. Between reports the
        clock runs on, so the dots move at the frame rate — but only for a
        quarter second, so they come to rest instead of drifting on when
        playback stops.
        """
        ahead = min(self.REST_COAST, max(0.0, time.monotonic() - self._reported))
        return self.position + ahead

    # ------------------------------------------------------------- the type

    def _font(self, option, active: bool) -> QFont:
        font = QFont(option.font)
        font.setWeight(QFont.Weight.Bold)
        if active and self.grow:
            size = font.pointSizeF()
            if size > 0:
                font.setPointSizeF(size * self.GROW)
            else:
                font.setPixelSize(max(1, int(font.pixelSize() * self.GROW)))
        return font

    def _strength(self, row: int, sung: bool) -> float:
        """How present a line is: brightest at the voice, fading outwards."""
        if self.plain:
            return self.PLAIN
        if self.row < 0:
            return self.COMING
        if row == self.row:
            return self.ACTIVE
        away = abs(row - self.row)
        base = self.GONE if sung else self.COMING
        return max(self.DIMMEST, base - self.FADE * (away - 1))

    def _shade(self, strength: float) -> QColor:
        colour = QColor(self.ink)
        colour.setAlphaF(max(0.0, min(1.0, strength)))
        return colour

    # ----------------------------------------------------------- the laying

    def _layout(self, line, font: QFont) -> QTextLayout:
        text = line[1] if isinstance(line, tuple) else str(line)
        layout = QTextLayout(text or " ", font)
        options = QTextOption(
            (Qt.AlignmentFlag.AlignHCenter if self.centred
             else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(options)
        return layout

    def _runs(self, line, row: int) -> list:
        """The colour runs for one line, as (start, length, colour)."""
        text = line[1] if isinstance(line, tuple) else str(line)
        if not text:
            return []
        if row != self.row:
            return [(0, len(text), self._shade(self._strength(row, row < self.row)))]
        words = getattr(line, "words", ())
        if not (self.enabled and words):
            return [(0, len(text), self._shade(self.ACTIVE))]
        through = line.sung_through(self.position)
        if through <= 0:
            return [(0, len(text), self._shade(self.COMING))]
        if through >= len(text):
            return [(0, len(text), self._shade(self.ACTIVE))]
        # A character of overlap softens the edge between the two.
        return [(0, through, self._shade(self.ACTIVE)),
                (through, 1, self._shade((self.ACTIVE + self.COMING) / 2)),
                (through + 1, len(text) - through - 1, self._shade(self.COMING))]

    def _lay_out(self, layout: QTextLayout, width: int) -> float:
        layout.beginLayout()
        height = 0.0
        while True:
            textline = layout.createLine()
            if not textline.isValid():
                break
            textline.setLineWidth(width)
            textline.setPosition(QPointF(0, height))
            height += textline.height() * LINE_SPACING
        layout.endLayout()
        return height

    @staticmethod
    def _run(start: int, length: int, colour: QColor) -> QTextLayout.FormatRange:
        run = QTextLayout.FormatRange()
        run.start, run.length = start, length
        run.format.setForeground(colour)
        return run

    # -------------------------------------------------------------- drawing

    @staticmethod
    def _eased(amount: float) -> float:
        """Smoothstep: no corner at either end of a dot's growth."""
        amount = max(0.0, min(amount, 1.0))
        return amount * amount * (3.0 - 2.0 * amount)

    def _paint_rest(self, painter: QPainter, rect, line, active: bool) -> None:
        """Three dots for an instrumental, growing as the singing comes back."""
        begin, until = line.rest
        span = max(0.001, until - begin)
        through = (max(0.0, min((self._running() - begin) / span, 1.0))
                   if active else 0.0)
        # Normalised against REST_DONE, so the last dot reaches its full size
        # a moment before the words return rather than on the very last frame.
        grown = min(through / self.REST_DONE, 1.0)
        size = min(rect.height() * 0.5, rect.width() / 9)
        middle = rect.center()
        # A slow swell underneath it all, so a long break is never still.
        breath = 1.0 + (0.035 * math.sin(self._running() * 1.9) if active else 0.0)
        painter.setPen(Qt.PenStyle.NoPen)
        for offset, dot in enumerate((-1, 0, 1)):
            share = self._eased((grown - offset * self.REST_STEP) / self.REST_RISE)
            radius = size * (0.22 + 0.46 * share) * breath
            colour = QColor(self.ink)
            colour.setAlphaF(max(0.0, min(self.COMING * (0.5 + 0.5 * share) if active
                                          else self.DIMMEST, 1.0)))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(middle.x() + dot * size * 1.55,
                                        middle.y()), radius, radius)
        self._resting = active
        # Only while positions are still arriving: paused, the dots hold where
        # they are instead of the timer running on for nothing.
        if (active and not self._frames.isActive()
                and time.monotonic() - self._reported < self.REST_COAST):
            self._frames.start()

    def _blur_for(self, row: int) -> float:
        """How soft a line is, by how far it is from the one being sung."""
        if not self.blur or self.plain or self.row < 0 or row == self.row:
            return 0.0
        away = abs(row - self.row)
        reach = self.BLUR_STEP * self.blur * (self.BLUR_BEHIND if row < self.row else 1.0)
        return min(away * reach, self.BLUR_MOST * self.blur)

    @staticmethod
    def _soften(source: QImage, radius: float) -> QImage:
        """Blur, properly.

        Qt will not blur a painter's output directly, but it will blur a
        graphics item — so the line goes into a scene of its own with a blur
        on it and is rendered back out. That is Qt's own blur in C++, which
        is what makes it look like depth of field instead of a small picture
        stretched too far. It happens once per line and is then kept.
        """
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(QPixmap.fromImage(source))
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        out = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(0)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scene.render(painter, QRectF(out.rect()), QRectF(source.rect()))
        painter.end()
        scene.clear()
        return out

    def _soft_line(self, line, row: int, option, width: int, radius: float):
        """The line, drawn once and softened, kept for as long as it is wanted."""
        text = line[1] if isinstance(line, tuple) else str(line)
        shade = self._shade(self._strength(row, row < self.row))
        key = (text, width, round(radius, 1), option.font.pixelSize(),
               option.font.pointSize(), shade.alpha())
        found = self._soft.get(key)
        if found is not None:
            return found
        layout = self._layout(line, self._font(option, False))
        layout.setFormats([self._run(0, len(text or " "), shade)])
        height = self._lay_out(layout, width)
        pad = int(radius * 3) + 2
        image = QImage(width + pad * 2, max(1, int(height)) + pad * 2,
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        into = QPainter(image)
        into.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        layout.draw(into, QPointF(pad, pad))
        into.end()
        made = (QPixmap.fromImage(self._soften(image, radius)), pad, height)
        if len(self._soft) > 96:
            self._soft.clear()         # a new song, or a new size
        self._soft[key] = made
        return made

    def paint(self, painter: QPainter, option, index) -> None:
        line = index.data(self.line_role)
        if line is None:
            super().paint(painter, option, index)
            return
        active = index.row() == self.row
        if getattr(line, "rest", None):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_rest(painter, option.rect, line, active)
            painter.restore()
            return
        if active:
            self._resting = False      # the break, if any, is behind us
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = option.rect.adjusted(self.padding, 0, -self.padding, 0)
        radius = self._blur_for(index.row())
        if radius >= 0.4:
            soft, pad, height = self._soft_line(line, index.row(), option,
                                                rect.width(), radius)
            top = rect.top() + max(0.0, (rect.height() - height) / 2)
            painter.drawPixmap(QPointF(rect.left() - pad, top - pad), soft)
            painter.restore()
            return
        layout = self._layout(line, self._font(option, active))
        layout.setFormats([self._run(start, length, colour)
                           for start, length, colour in self._runs(line, index.row())
                           if length > 0])
        height = self._lay_out(layout, rect.width())
        top = rect.top() + max(0.0, (rect.height() - height) / 2)
        origin = QPointF(rect.left(), top)
        if active and self.glow.alpha():
            # Qt will not blur text in a delegate at any sensible cost, so the
            # light behind the line is a few faint copies nudged around it.
            # A second layout, because changing a layout's formats after it
            # has been laid out throws the layout away and it draws nothing.
            halo = self._layout(line, self._font(option, True))
            halo.setFormats([self._run(0, len(line[1] or " "), self.glow)])
            self._lay_out(halo, rect.width())
            for shift in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                halo.draw(painter, origin + QPointF(*shift))
        layout.draw(painter, origin)
        painter.restore()

    def sizeHint(self, option, index):
        line = index.data(self.line_role)
        if line is None:
            return super().sizeHint(option, index)
        if getattr(line, "rest", None):
            hint = super().sizeHint(option, index)
            hint.setHeight(max(24, int(QFontMetrics(option.font).height() * 1.6)))
            return hint
        # Measured as it is drawn when sung — a little larger — so the line
        # lighting up can never wrap onto a row the next line is sitting on.
        layout = self._layout(line, self._font(option, True))
        height = self._lay_out(layout, self._room(option))
        hint = super().sizeHint(option, index)
        hint.setHeight(max(18, int(height + 0.999) + LINE_GAP))
        return hint

    def _room(self, option) -> int:
        """How wide a line may be drawn.

        The option's rectangle is the row being painted, and while a row's
        height is being asked for there is not one yet — so the view's own
        viewport is what to measure against. Getting this wrong makes every
        line wrap to a sliver and the rows grow enormous.
        """
        view = self.parent()
        room = view.viewport().width() if hasattr(view, "viewport") else 0
        return max(60, (room or option.rect.width()) - self.padding * 2 - 2)


def chosen_glass(library) -> str:
    """How see-through the window should be, including the older on/off form."""
    level = str(library.get("glass", "") or "")
    if level in style.GLASS_LEVELS:
        return level
    return "frosted" if library.get("translucent", True) else "solid"


def glass_menu(menu, library, changed) -> None:
    """A *Background* submenu: solid, frosted or clear.

    Frosted and clear only look like glass if the compositor blurs behind the
    window — KWin's Blur effect, or Better Blur. Without one they simply show
    the desktop through the app, which is nobody's idea of a window.
    """
    current = chosen_glass(library)
    submenu = menu.addMenu("Background")
    for level, label in (("solid", "Solid"),
                         ("frosted", "Frosted glass"),
                         ("clear", "Clear glass")):
        action = submenu.addAction(label)
        action.setCheckable(True)
        action.setChecked(level == current)
        action.triggered.connect(lambda _checked=False, chosen=level: changed(chosen))


def format_time(seconds: float, force_hours: bool = False) -> str:
    seconds = max(0, int(round(seconds or 0)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours or force_hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "--"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


class SeekBar(QWidget):
    """A scrub bar that seeks on click, scrubs on drag and previews on hover."""

    seeked = Signal(float)          # committed position in seconds
    scrubbing = Signal(float)       # live position while dragging
    scrubStateChanged = Signal(bool)
    hovered = Signal(float, int)    # seconds under the cursor, x in widget coords
    unhovered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._duration = 0.0
        self._position = 0.0
        self._marks: list[float] = []
        self._hover_ratio: float | None = None
        self._dragging = False

    # ------------------------------------------------------------------ state

    def set_duration(self, duration: float) -> None:
        self._duration = max(0.0, float(duration or 0))
        self.update()

    def set_position(self, position: float) -> None:
        if self._dragging:
            return
        self._position = max(0.0, float(position or 0))
        self.update()

    def set_marks(self, marks: list[float]) -> None:
        """Chapter boundaries, drawn as ticks along the bar."""
        self._marks = [float(m) for m in marks]
        self.update()

    @property
    def dragging(self) -> bool:
        return self._dragging

    def _ratio_at(self, x: int) -> float:
        width = max(1, self.width() - 2)
        return min(1.0, max(0.0, (x - 1) / width))

    # ----------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._duration > 0:
            self._dragging = True
            self.scrubStateChanged.emit(True)
            self._position = self._ratio_at(int(event.position().x())) * self._duration
            self.scrubbing.emit(self._position)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        x = int(event.position().x())
        if self._duration > 0:
            self._hover_ratio = self._ratio_at(x)
            self.hovered.emit(self._hover_ratio * self._duration, x)
        if self._dragging and self._duration > 0:
            self._position = self._ratio_at(x) * self._duration
            self.scrubbing.emit(self._position)
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.scrubStateChanged.emit(False)
            self.seeked.emit(self._position)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover_ratio = None
        self.unhovered.emit()
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        if self._duration > 0:
            step = 5 if event.angleDelta().y() > 0 else -5
            self.seeked.emit(max(0.0, min(self._duration, self._position + step)))
            event.accept()
            return
        super().wheelEvent(event)

    # ---------------------------------------------------------------- painting

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        active = self._dragging or self._hover_ratio is not None
        height = 7.0 if active else 5.0
        top = (self.height() - height) / 2
        width = max(1.0, self.width() - 2.0)
        radius = height / 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 38))
        painter.drawRoundedRect(QRectF(1, top, width, height), radius, radius)

        if self._hover_ratio is not None and not self._dragging:
            painter.setBrush(QColor(255, 255, 255, 55))
            painter.drawRoundedRect(QRectF(1, top, width * self._hover_ratio, height), radius, radius)

        ratio = (self._position / self._duration) if self._duration > 0 else 0.0
        ratio = min(1.0, max(0.0, ratio))
        if ratio > 0:
            painter.setBrush(QColor(style.ACCENT))
            painter.drawRoundedRect(QRectF(1, top, max(radius * 2, width * ratio), height),
                                    radius, radius)
        # Chapter ticks go on last, so they notch the played part too.
        if self._marks and self._duration > 0:
            painter.setBrush(QColor(11, 12, 16, 225))
            for mark in self._marks:
                if 0 < mark < self._duration:
                    x = 1 + width * (mark / self._duration)
                    painter.drawRect(QRectF(x - 0.9, top, 1.8, height))

        if active:
            centre = 1 + width * ratio
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QRectF(centre - 7, self.height() / 2 - 7, 14, 14))
            painter.setBrush(QColor(style.ACCENT))
            painter.drawEllipse(QRectF(centre - 4, self.height() / 2 - 4, 8, 8))


class VolumeBar(QWidget):
    """0–200 % volume with a detent marker at 100 %."""

    changed = Signal(float)

    def __init__(self, parent=None, maximum: float = 200.0):
        super().__init__(parent)
        self.setFixedSize(96, 20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._maximum = maximum
        self._value = 100.0
        self._muted = False
        self._dragging = False
        self._hover = False

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(self._maximum, float(value or 0)))
        self.update()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.update()

    def _apply(self, x: int) -> None:
        width = max(1, self.width() - 8)
        ratio = min(1.0, max(0.0, (x - 4) / width))
        value = ratio * self._maximum
        # Snap to 100 % so the neutral level is easy to hit.
        if abs(value - 100.0) < 4:
            value = 100.0
        self._value = value
        self.changed.emit(value)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._apply(int(event.position().x()))
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        self._hover = True
        if self._dragging:
            self._apply(int(event.position().x()))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        event.accept()

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        delta = 5 if event.angleDelta().y() > 0 else -5
        self._value = max(0.0, min(self._maximum, self._value + delta))
        self.changed.emit(self._value)
        self.update()
        event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        height = 5.0
        top = (self.height() - height) / 2
        width = float(self.width() - 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 38))
        painter.drawRoundedRect(QRectF(4, top, width, height), 2.5, 2.5)

        ratio = self._value / self._maximum
        colour = QColor(style.FAINT) if self._muted else QColor(style.ACCENT)
        if self._value > 100.0 and not self._muted:
            colour = QColor(style.WARN)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(4, top, max(2.0, width * ratio), height), 2.5, 2.5)

        # Unity marker.
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        unity = 4 + width * (100.0 / self._maximum)
        painter.drawLine(int(unity), int(top - 2), int(unity), int(top + height + 2))

        if self._hover or self._dragging:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QRectF(4 + width * ratio - 5, self.height() / 2 - 5, 10, 10))


class Toast(QLabel):
    """Transient centred message over the video ("Volume 85 %", "+5s", …)."""

    def __init__(self, parent=None, object_name: str = "Toast", anchor: str = "top"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._anchor = anchor
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)

    def flash(self, text: str, msec: int = 1300) -> None:
        self.setText(text)
        self.adjustSize()
        if self.parent() is not None:
            self._reposition()
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(msec)

    def show_sticky(self, text: str) -> None:
        self._hide_timer.stop()
        self.flash(text, 10**7)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        self._fade_out()

    def _fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        self._fade.finished.connect(self._maybe_hide)

    def _maybe_hide(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.hide()

    def _reposition(self) -> None:
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        x = int((parent.width() - self.width()) / 2)
        if self._anchor == "bottom":
            y = int(parent.height() * 0.80)
        else:
            y = int(parent.height() * 0.11)
        self.move(x, y)
