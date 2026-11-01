"""Vector icons drawn with QPainter, so the app carries no image assets."""

import math
import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)

_CACHE: dict[tuple, QIcon] = {}
MUTED_ARROW = "#939aab"


def _pen(painter: QPainter, color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    return pen


def _triangle(cx: float, cy: float, size: float) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(cx - size * 0.42, cy - size * 0.55)
    path.lineTo(cx + size * 0.58, cy)
    path.lineTo(cx - size * 0.42, cy + size * 0.55)
    path.closeSubpath()
    return path


def _draw(name: str, painter: QPainter, size: int, color: QColor) -> None:
    """Draw `name` into a size x size box. Coordinates use a 24-unit grid."""
    scale = size / 24.0
    painter.scale(scale, scale)
    stroke = 2.0
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _pen(painter, color, stroke)

    if name == "play":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_triangle(12, 12, 13))
    elif name == "pause":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(6.5, 4.5, 3.8, 15), 1.4, 1.4)
        painter.drawRoundedRect(QRectF(13.7, 4.5, 3.8, 15), 1.4, 1.4)
    elif re.fullmatch(r"(back|forward)\d+", name):
        forward = name.startswith("forward")
        label = re.sub(r"[^0-9]", "", name)
        painter.save()
        if forward:
            painter.translate(24, 0)
            painter.scale(-1, 1)
        path = QPainterPath()
        rect = QRectF(3.5, 3.5, 17, 17)
        path.arcMoveTo(rect, 70)
        path.arcTo(rect, 70, 290)
        painter.drawPath(path)
        head = QPainterPath()
        head.moveTo(9.2, 1.6)
        head.lineTo(5.6, 4.9)
        head.lineTo(9.6, 7.4)
        painter.drawPath(head)
        painter.restore()
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(2, 8.5, 20, 12), Qt.AlignmentFlag.AlignCenter, label)
    elif name in ("prev", "next"):
        painter.save()
        if name == "prev":
            painter.translate(24, 0)
            painter.scale(-1, 1)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_triangle(10, 12, 11))
        painter.drawRoundedRect(QRectF(16.4, 5.4, 3.0, 13.2), 1.2, 1.2)
        painter.restore()
    elif name in ("volume", "volume_low", "volume_mute", "volume_boost"):
        speaker = QPainterPath()
        speaker.moveTo(4, 9.5)
        speaker.lineTo(7.5, 9.5)
        speaker.lineTo(12, 5)
        speaker.lineTo(12, 19)
        speaker.lineTo(7.5, 14.5)
        speaker.lineTo(4, 14.5)
        speaker.closeSubpath()
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(speaker)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _pen(painter, color, 1.8)
        if name == "volume_mute":
            painter.drawLine(QPointF(15.5, 9), QPointF(21, 15))
            painter.drawLine(QPointF(21, 9), QPointF(15.5, 15))
        else:
            arcs = [(QRectF(9.5, 8.2, 7, 7.6), 1)]
            if name in ("volume", "volume_boost"):
                arcs.append((QRectF(7.5, 5.4, 12, 13.2), 1))
            if name == "volume_boost":
                arcs.append((QRectF(5.5, 2.6, 17, 18.8), 1))
            for rect, _ in arcs:
                path = QPainterPath()
                path.arcMoveTo(rect, -55)
                path.arcTo(rect, -55, 110)
                painter.drawPath(path)
    elif name in ("fullscreen", "fullscreen_exit"):
        _pen(painter, color, 2.1)
        if name == "fullscreen":
            corners = [((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
                       ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15))]
        else:
            corners = [((4, 9), (9, 9), (9, 4)), ((15, 4), (15, 9), (20, 9)),
                       ((20, 15), (15, 15), (15, 20)), ((9, 20), (9, 15), (4, 15))]
        for a, b, c in corners:
            path = QPainterPath(QPointF(*a))
            path.lineTo(QPointF(*b))
            path.lineTo(QPointF(*c))
            painter.drawPath(path)
    elif name == "gear":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(8):
            painter.save()
            painter.translate(12, 12)
            painter.rotate(index * 45)
            painter.drawRoundedRect(QRectF(-1.5, -10.6, 3.0, 5.0), 1.0, 1.0)
            painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _pen(painter, color, 2.4)
        painter.drawEllipse(QPointF(12, 12), 5.4, 5.4)
        _pen(painter, color, 2.0)
        painter.drawEllipse(QPointF(12, 12), 2.4, 2.4)
    elif name == "back":
        _pen(painter, color, 2.2)
        painter.drawLine(QPointF(20, 12), QPointF(5, 12))
        path = QPainterPath(QPointF(11.5, 5.5))
        path.lineTo(4.8, 12)
        path.lineTo(11.5, 18.5)
        painter.drawPath(path)
    elif name == "folder_add":
        _pen(painter, color, 1.9)
        path = QPainterPath(QPointF(3, 19))
        path.lineTo(3, 6)
        path.lineTo(9.5, 6)
        path.lineTo(11.5, 8.6)
        path.lineTo(21, 8.6)
        path.lineTo(21, 19)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(12, 11.4), QPointF(12, 16.6))
        painter.drawLine(QPointF(9.4, 14), QPointF(14.6, 14))
    elif name == "refresh":
        _pen(painter, color, 2.0)
        rect = QRectF(4, 4, 16, 16)
        path = QPainterPath()
        path.arcMoveTo(rect, 60)
        path.arcTo(rect, 60, 280)
        painter.drawPath(path)
        head = QPainterPath(QPointF(13.4, 2.2))
        head.lineTo(17.4, 5.2)
        head.lineTo(13.2, 7.8)
        painter.drawPath(head)
    elif name == "search":
        _pen(painter, color, 2.0)
        painter.drawEllipse(QPointF(10.6, 10.6), 6.0, 6.0)
        painter.drawLine(QPointF(15.2, 15.2), QPointF(20.4, 20.4))
    elif name == "subtitles":
        _pen(painter, color, 1.8)
        painter.drawRoundedRect(QRectF(3, 5.5, 18, 13), 2.6, 2.6)
        _pen(painter, color, 2.0)
        painter.drawLine(QPointF(6.4, 11.6), QPointF(11.2, 11.6))
        painter.drawLine(QPointF(13.6, 11.6), QPointF(17.6, 11.6))
        painter.drawLine(QPointF(6.4, 15.0), QPointF(9.2, 15.0))
        painter.drawLine(QPointF(11.6, 15.0), QPointF(17.6, 15.0))
    elif name == "check":
        _pen(painter, color, 2.4)
        path = QPainterPath(QPointF(4.5, 12.6))
        path.lineTo(9.6, 17.6)
        path.lineTo(19.5, 6.6)
        painter.drawPath(path)
    elif name == "close":
        _pen(painter, color, 2.2)
        painter.drawLine(QPointF(6, 6), QPointF(18, 18))
        painter.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "grid":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for x in (3.5, 13.0):
            for y in (3.5, 13.0):
                painter.drawRoundedRect(QRectF(x, y, 7.5, 7.5), 1.8, 1.8)
    elif name == "clock":
        _pen(painter, color, 1.9)
        painter.drawEllipse(QPointF(12, 12), 8.4, 8.4)
        painter.drawLine(QPointF(12, 7.4), QPointF(12, 12.4))
        painter.drawLine(QPointF(12, 12.4), QPointF(15.6, 14.4))
    elif name == "eye":
        _pen(painter, color, 1.9)
        path = QPainterPath(QPointF(2.6, 12))
        path.quadTo(12, 4.2, 21.4, 12)
        path.quadTo(12, 19.8, 2.6, 12)
        painter.drawPath(path)
        painter.drawEllipse(QPointF(12, 12), 2.8, 2.8)
    elif name == "film":
        _pen(painter, color, 1.8)
        painter.drawRoundedRect(QRectF(3, 5, 18, 14), 2.4, 2.4)
        painter.drawLine(QPointF(8, 5), QPointF(8, 19))
        painter.drawLine(QPointF(16, 5), QPointF(16, 19))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for y in (7.6, 15.4):
            painter.drawRoundedRect(QRectF(4.4, y, 2.2, 1.8), 0.6, 0.6)
            painter.drawRoundedRect(QRectF(17.4, y, 2.2, 1.8), 0.6, 0.6)
    elif name == "book":
        _pen(painter, color, 1.9)
        path = QPainterPath(QPointF(4, 4.5))
        path.lineTo(4, 19.5)
        path.quadTo(4, 17.6, 7.2, 17.6)
        path.lineTo(20, 17.6)
        painter.drawPath(path)
        painter.drawLine(QPointF(20, 17.6), QPointF(20, 3.2))
        painter.drawLine(QPointF(20, 3.2), QPointF(7.2, 3.2))
        painter.drawLine(QPointF(7.2, 3.2), QPointF(4, 4.5))
        painter.drawLine(QPointF(7.2, 3.2), QPointF(7.2, 17.6))
    elif name == "headphones":
        _pen(painter, color, 2.0)
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(4, 4, 16, 16), 20)
        arc.arcTo(QRectF(4, 4, 16, 16), 20, 140)
        painter.drawPath(arc)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(3.4, 12.2, 4.2, 8), 2.0, 2.0)
        painter.drawRoundedRect(QRectF(16.4, 12.2, 4.2, 8), 2.0, 2.0)
    elif name == "moon":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        crescent = QPainterPath()
        crescent.addEllipse(QPointF(12, 12), 9.0, 9.0)
        cut = QPainterPath()
        cut.addEllipse(QPointF(16.4, 8.6), 8.2, 8.2)
        painter.drawPath(crescent.subtracted(cut))
    elif name == "sun":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(12, 12), 4.6, 4.6)
        _pen(painter, color, 1.9)
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1, y1 = 12 + 7.2 * math.cos(rad), 12 + 7.2 * math.sin(rad)
            x2, y2 = 12 + 9.6 * math.cos(rad), 12 + 9.6 * math.sin(rad)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name in ("bookmark", "bookmark_add"):
        _pen(painter, color, 1.9)
        path = QPainterPath(QPointF(7, 3.5))
        path.lineTo(17, 3.5)
        path.lineTo(17, 20.5)
        path.lineTo(12, 16.2)
        path.lineTo(7, 20.5)
        path.closeSubpath()
        if name == "bookmark":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        if name == "bookmark_add":
            _pen(painter, color, 1.8)
            painter.drawLine(QPointF(12, 7.4), QPointF(12, 12.2))
            painter.drawLine(QPointF(9.6, 9.8), QPointF(14.4, 9.8))
    elif name == "list":
        _pen(painter, color, 2.0)
        for y in (6.5, 12, 17.5):
            painter.drawLine(QPointF(9, y), QPointF(20, y))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for y in (6.5, 12, 17.5):
            painter.drawEllipse(QPointF(5, y), 1.5, 1.5)
    elif name in ("heart", "heart_filled"):
        path = QPainterPath()
        path.moveTo(12, 20.2)
        path.cubicTo(3.4, 14.6, 2.6, 9.4, 5.6, 6.6)
        path.cubicTo(8.2, 4.2, 11.2, 5.6, 12, 8.0)
        path.cubicTo(12.8, 5.6, 15.8, 4.2, 18.4, 6.6)
        path.cubicTo(21.4, 9.4, 20.6, 14.6, 12, 20.2)
        path.closeSubpath()
        if name == "heart_filled":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            _pen(painter, color, 1.9)
        painter.drawPath(path)
    elif name in ("shuffle", "shuffle_albums"):
        _pen(painter, color, 1.9)
        upper = QPainterPath(QPointF(3, 7))
        upper.lineTo(7.5, 7)
        upper.cubicTo(11, 7, 13, 17, 16.5, 17)
        upper.lineTo(20, 17)
        painter.drawPath(upper)
        lower = QPainterPath(QPointF(3, 17))
        lower.lineTo(7.5, 17)
        lower.cubicTo(9.4, 17, 10.8, 14, 12, 12)
        painter.drawPath(lower)
        second = QPainterPath(QPointF(14.6, 9.4))
        second.cubicTo(15.4, 8, 16, 7, 16.5, 7)
        second.lineTo(20, 7)
        painter.drawPath(second)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for y in (7, 17):
            head = QPainterPath(QPointF(18.4, y - 2.8))
            head.lineTo(21.6, y)
            head.lineTo(18.4, y + 2.8)
            head.closeSubpath()
            painter.drawPath(head)
    elif name in ("repeat", "repeat_one"):
        _pen(painter, color, 1.9)
        painter.drawLine(QPointF(7, 6.5), QPointF(16.5, 6.5))
        painter.drawLine(QPointF(17, 17.5), QPointF(7.5, 17.5))
        arc_right = QPainterPath()
        arc_right.arcMoveTo(QRectF(12.5, 6.5, 8, 11), 90)
        arc_right.arcTo(QRectF(12.5, 6.5, 8, 11), 90, -180)
        painter.drawPath(arc_right)
        arc_left = QPainterPath()
        arc_left.arcMoveTo(QRectF(3.5, 6.5, 8, 11), 90)
        arc_left.arcTo(QRectF(3.5, 6.5, 8, 11), 90, 180)
        painter.drawPath(arc_left)
        if name == "repeat_one":
            font = painter.font()
            font.setPixelSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(QRectF(8, 7.5, 8, 9), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "queue":
        _pen(painter, color, 2.0)
        for y in (6, 11, 16):
            painter.drawLine(QPointF(3.5, y), QPointF(14 if y == 16 else 20.5, y))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_triangle(18, 17.4, 7))
    elif name == "note":
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(8, 17.5), 3.4, 2.8)
        painter.drawEllipse(QPointF(17.5, 15), 3.4, 2.8)
        painter.drawRoundedRect(QRectF(10.4, 5.4, 1.9, 12.4), 0.9, 0.9)
        painter.drawRoundedRect(QRectF(19.0, 3.2, 1.9, 12), 0.9, 0.9)
        painter.drawPolygon([QPointF(10.4, 5.4), QPointF(20.9, 3.0),
                             QPointF(20.9, 6.4), QPointF(10.4, 8.8)])
    elif name == "device":
        _pen(painter, color, 1.9)
        painter.drawRoundedRect(QRectF(7, 2.6, 10, 18.8), 2.4, 2.4)
        painter.drawLine(QPointF(10.4, 5.4), QPointF(13.6, 5.4))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(12, 18.2), 1.2, 1.2)
    elif name == "sync":
        _pen(painter, color, 2.0)
        top = QPainterPath()
        top.arcMoveTo(QRectF(4, 4, 16, 16), 30)
        top.arcTo(QRectF(4, 4, 16, 16), 30, 140)
        painter.drawPath(top)
        bottom = QPainterPath()
        bottom.arcMoveTo(QRectF(4, 4, 16, 16), 210)
        bottom.arcTo(QRectF(4, 4, 16, 16), 210, 140)
        painter.drawPath(bottom)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_triangle(18.6, 7.4, 6))
        painter.save()
        painter.translate(5.4, 16.6)
        painter.rotate(180)
        painter.drawPath(_triangle(0, 0, 6))
        painter.restore()
    elif name == "convert":
        _pen(painter, color, 2.0)
        painter.drawLine(QPointF(4, 8.5), QPointF(16, 8.5))
        painter.drawLine(QPointF(20, 15.5), QPointF(8, 15.5))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(_triangle(17.4, 8.5, 6))
        painter.save()
        painter.translate(6.6, 15.5)
        painter.rotate(180)
        painter.drawPath(_triangle(0, 0, 6))
        painter.restore()
    elif name == "lyrics":
        _pen(painter, color, 1.9)
        painter.drawRoundedRect(QRectF(3, 4.5, 18, 13.5), 2.6, 2.6)
        path = QPainterPath(QPointF(8, 18))
        path.lineTo(11, 21.4)
        path.lineTo(13.4, 18)
        painter.drawPath(path)
        _pen(painter, color, 1.7)
        painter.drawLine(QPointF(6.4, 9.2), QPointF(15.2, 9.2))
        painter.drawLine(QPointF(6.4, 12.4), QPointF(17.6, 12.4))
        painter.drawLine(QPointF(6.4, 15.4), QPointF(12.4, 15.4))
    elif name == "logo":
        _logo(painter, color)


def _logo_paths() -> tuple[QPainterPath, QPainterPath]:
    """The mark, on the 24-unit grid: an A whose crossbar is a play button.

    Aeris plays three things — films, books, songs — and the one control
    they all share is the triangle, so it takes the place of the bar. It
    sits in the counter of the A with clear air on both sides, which is what
    keeps it reading as two shapes rather than one smudge at 16 pixels.
    """
    legs = QPainterPath(QPointF(4.4, 20.4))
    legs.lineTo(12.0, 3.4)
    legs.lineTo(19.6, 20.4)
    play = QPainterPath()
    play.moveTo(10.0, 11.9)
    play.lineTo(14.1, 14.35)
    play.lineTo(10.0, 16.8)
    play.closeSubpath()
    return legs, play


def _logo(painter: QPainter, ink, weight: float = 2.8) -> None:
    """Draw the mark in `ink` — a colour, or a gradient in grid units."""
    legs, play = _logo_paths()
    brush = QBrush(ink)
    pen = QPen(brush, weight)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(legs)
    edge = QPen(brush, 1.1)            # softens the triangle's points
    edge.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(edge)
    painter.setBrush(brush)
    painter.drawPath(play)


def icon(name: str, size: int = 24, color: str = "#e8eaf0") -> QIcon:
    key = (name, size, color)
    # Skip cache for logo to allow live updates during development
    if name == "logo":
        _CACHE.pop(key, None)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    _draw(name, painter, size, QColor(color))
    painter.end()
    result = QIcon(pixmap)
    _CACHE[key] = result
    return result


def _slug(color: str) -> str:
    """A colour as something safe to put in a file name."""
    return "-" + "".join(c for c in str(color).lower() if c.isalnum())


def write_chevron(color: str = MUTED_ARROW, path=None) -> str:
    """Render the combo-box arrow to a PNG that the stylesheet can point at.

    Colour first: every caller has one and none has ever passed a path, and
    the other way round meant `write_chevron(style.MUTED)` quietly tried to
    save the arrow to a file called "#8fb2c9" — which has no extension, so Qt
    could not tell what format to write and the arrow was never drawn at all.
    """
    from .paths import CACHE_DIR

    # Named after the colour: Qt caches stylesheet images by their address,
    # so reusing one name would leave the old theme's arrow on screen.
    target = Path(path) if path else CACHE_DIR / f"chevron{_slug(color)}.png"
    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _pen(painter, QColor(color), 2.4)
    path_ = QPainterPath(QPointF(6, 9.5))
    path_.lineTo(12, 15.5)
    path_.lineTo(18, 9.5)
    painter.drawPath(path_)
    painter.end()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if pixmap.save(str(target)):
            return str(target)
    except OSError:
        pass
    return ""


def write_tick(color: str = "#ffffff", path=None) -> str:
    """Render the checkbox tick to a PNG the stylesheet can point at.

    A checked box with no tick in it reads as a coloured square, and whether a
    coloured square means yes or no is anybody's guess. Qt will not draw one
    over a styled indicator, so it has to be supplied as an image.
    """
    from .paths import CACHE_DIR

    target = Path(path) if path else CACHE_DIR / f"tick{_slug(color)}.png"
    size = 18
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _pen(painter, QColor(color), 2.3)
    stroke = QPainterPath(QPointF(4.0, 9.3))
    stroke.lineTo(7.4, 12.8)
    stroke.lineTo(14.0, 5.6)
    painter.drawPath(stroke)
    painter.end()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if pixmap.save(str(target)):
            return str(target)
    except OSError:
        pass
    return ""


# Frutiger Aero, as the default theme has it: glass the colour of deep water,
# a sheen across the top half, a bright rim, and the logo lit from inside.
# The blue-to-mauve of the mark and the light behind it carry over unchanged.
TILE_TOP, TILE_BOTTOM = "#2379ad", "#06121d"
RIM = "#3a6b85"                                # Aero Night's border colour
INK_FROM, INK_TO = "#89b4fa", "#d0a8f8"


def _halo(painter: QPainter, colour: QColor, weight: float) -> None:
    """A soft light around the mark: the glow Aero put behind its text.

    Painted as the mark again in wide, faint strokes, widest and faintest
    first, which needs nothing but QPainter — so the installer, which runs
    without a full application, can draw it too.
    """
    legs, play = _logo_paths()
    # Many thin layers rather than a few thick ones: a few show as rings.
    layers = 12
    for step in range(layers):
        spread = 2.3 * (1.0 - step / layers) + 0.15
        tint = QColor(colour)
        tint.setAlpha(5 + step)
        pen = QPen(tint, weight + spread * 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(legs)
    # The triangle gets a much tighter light of its own, or it fills the
    # counter of the A and the two shapes run together.
    tint = QColor(colour)
    tint.setAlpha(40)
    pen = QPen(tint, 2.2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPath(play)


def app_icon(size: int = 128, color: str = INK_FROM,
             background: str = TILE_BOTTOM) -> QIcon:
    """The app's tile: a pane of Aero glass with the mark lit inside it.

    `color` starts the mark's gradient and `background` is the bottom of the
    glass, so another theme can be passed in.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    radius = size * 0.23
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # The glass: deep water, darker at the foot.
    base = QLinearGradient(0, 0, 0, size)
    base.setColorAt(0.0, QColor(TILE_TOP))
    base.setColorAt(0.5, QColor("#0f3a5a"))
    base.setColorAt(1.0, QColor(background))
    painter.fillPath(tile, base)

    # Light pooling behind the mark, in the mark's own colours.
    glow = QRadialGradient(size * 0.5, size * 0.56, size * 0.58)
    start = QColor(color)
    start.setAlpha(120)
    middle = QColor(INK_TO)
    middle.setAlpha(52)
    clear = QColor(INK_TO)
    clear.setAlpha(0)
    glow.setColorAt(0.0, start)
    glow.setColorAt(0.55, middle)
    glow.setColorAt(1.0, clear)
    painter.fillPath(tile, glow)

    # A bounce of cyan off the bottom edge, the way light comes back up
    # through Aero glass.
    bounce = QRadialGradient(size * 0.5, size * 1.08, size * 0.62)
    bounce.setColorAt(0.0, QColor(110, 220, 255, 70))
    bounce.setColorAt(1.0, QColor(110, 220, 255, 0))
    painter.fillPath(tile, bounce)

    # The mark, haloed, then drawn.
    painter.save()
    inner = size * 0.62
    painter.translate((size - inner) / 2, (size - inner) / 2 + size * 0.01)
    painter.scale(inner / 24.0, inner / 24.0)
    weight = 3.2 if size <= 32 else 2.8
    if size > 24:
        _halo(painter, QColor("#a9c8ff"), weight)
    ink = QLinearGradient(4.0, 3.4, 20.0, 20.4)
    ink.setColorAt(0.0, QColor("#d9e8ff"))     # lit where the sheen falls
    ink.setColorAt(0.4, QColor(color))
    ink.setColorAt(1.0, QColor(INK_TO))
    _logo(painter, ink, weight)
    painter.restore()

    # The sheen: the top of the glass catching the light, ending in the
    # long shallow curve that every Aero button had.
    sheen = QPainterPath()
    sheen.moveTo(0, 0)
    sheen.lineTo(size, 0)
    sheen.lineTo(size, size * 0.40)
    sheen.cubicTo(size * 0.72, size * 0.50, size * 0.30, size * 0.52, 0, size * 0.47)
    sheen.closeSubpath()
    shine = QLinearGradient(0, 0, 0, size * 0.52)
    shine.setColorAt(0.0, QColor(255, 255, 255, 105))
    shine.setColorAt(0.3, QColor(255, 255, 255, 38))
    shine.setColorAt(1.0, QColor(255, 255, 255, 6))
    painter.fillPath(tile.intersected(sheen), shine)

    # The rim: dark outside, bright inside along the top, as thick glass is.
    line = max(1.0, size / 96)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outer = QColor(RIM)
    outer.setAlpha(230)
    painter.setPen(QPen(outer, line))
    half = line / 2
    painter.drawRoundedRect(QRectF(half, half, size - line, size - line), radius, radius)
    highlight = QLinearGradient(0, 0, 0, size)
    highlight.setColorAt(0.0, QColor(255, 255, 255, 150))
    highlight.setColorAt(0.5, QColor(255, 255, 255, 30))
    highlight.setColorAt(1.0, QColor(160, 230, 255, 70))
    painter.setPen(QPen(QBrush(highlight), line))
    step = line * 1.5
    painter.drawRoundedRect(QRectF(step, step, size - step * 2, size - step * 2),
                            radius - step, radius - step)
    painter.end()
    return QIcon(pixmap)
