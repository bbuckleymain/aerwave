"""The small amount of movement the interface has, and the rules for it.

Motion here is feedback, not performance: a button answers a press, a seek bar
lifts under the pointer, a big change of page dissolves instead of cutting.
Nothing animates that happens tens of times an hour, and nothing started from
the keyboard animates at all, because a shortcut that makes you wait feels
broken by the hundredth time.
"""

import configparser
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QSize, Qt, QVariantAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

PRESS_MS = 100
HOVER_MS = 120
PAGE_MS = 200
PRESS_SCALE = 0.86             # how far an icon gives under a press


def _curve() -> QEasingCurve:
    """A strong ease-out: moves at once, settles slowly. The built-in curves
    are too timid to read as a response."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0))
    return curve


_enabled: bool | None = None


def enabled() -> bool:
    """False when the desktop has animations switched off."""
    global _enabled
    if _enabled is None:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(Path.home() / ".config" / "kdeglobals", encoding="utf-8")
            factor = parser.getfloat("KDE", "AnimationDurationFactor", fallback=1.0)
        except (OSError, ValueError, configparser.Error):
            factor = 1.0
        _enabled = factor > 0
    return _enabled


def tween(owner: QObject, start: float, end: float, ms: int, step) -> QVariantAnimation | None:
    """Run `step(value)` from start to end. Without motion it just lands."""
    if not enabled():
        step(end)
        return None
    animation = QVariantAnimation(owner)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setDuration(ms)
    animation.setEasingCurve(_curve())
    animation.valueChanged.connect(step)
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def press_dip(button) -> None:
    """An icon button gives slightly under a press and springs back.

    The icon shrinks rather than the button, which keeps the layout still.
    Asks for nothing until the button is pressed, so a window full of these
    costs nothing while the pointer is elsewhere.
    """
    full = button.iconSize()
    running = {"animation": None}

    def scale_to(factor: float) -> None:
        if running["animation"] is not None:
            running["animation"].stop()
        current = button.iconSize().width() / max(1, full.width())

        def step(value) -> None:
            side = float(value)
            button.setIconSize(QSize(round(full.width() * side), round(full.height() * side)))

        running["animation"] = tween(button, current, factor, PRESS_MS, step)
        if running["animation"] is not None:
            running["animation"].destroyed.connect(lambda: running.update(animation=None))

    button.pressed.connect(lambda: scale_to(PRESS_SCALE))
    button.released.connect(lambda: scale_to(1.0))


def dissolve(host: QWidget) -> None:
    """Fade a picture of what `host` shows now over whatever it shows next.

    Call just before changing page. It works from a screenshot, so the page
    underneath is live from the first frame and nothing waits for the fade.
    """
    if not enabled() or not host.isVisible():
        return
    cover = QLabel(host)
    cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    cover.setPixmap(host.grab())
    cover.setGeometry(host.rect())
    effect = QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    cover.show()
    cover.raise_()
    animation = tween(cover, 1.0, 0.0, PAGE_MS, effect.setOpacity)
    if animation is None:
        cover.deleteLater()
    else:
        animation.finished.connect(cover.deleteLater)
