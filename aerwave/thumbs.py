"""Poster thumbnails, extracted with ffmpeg and cached on disk."""

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QPixmapCache

from . import media
from .covers import PICTURE_CACHE_KB
from .paths import THUMB_DIR

THUMB_WIDTH = 480


def cache_key(path: str, size: int, mtime: float) -> str:
    digest = hashlib.sha1(f"{path}|{size}|{int(mtime)}".encode()).hexdigest()
    return digest[:20]



def _key(video_id: int) -> str:
    return f"poster-{video_id}"

class _Signals(QObject):
    done = Signal(int, str)  # video_id, file path ("" on failure)


class _ThumbJob(QRunnable):
    def __init__(self, video_id: int, source: str, target: Path, duration: float, signals: _Signals):
        super().__init__()
        self.video_id = video_id
        self.source = source
        self.target = target
        self.duration = duration
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        # Sample a frame ~18% in, which usually clears intros and black leader.
        offset = 5.0
        if self.duration > 20:
            offset = min(self.duration * 0.18, 600.0)
        ok = self._grab(offset) or (offset > 1 and self._grab(1.0))
        self.signals.done.emit(self.video_id, str(self.target) if ok else "")

    def _grab(self, offset: float) -> bool:
        tmp = self.target.with_suffix(".part.jpg")
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{offset:.2f}",
            "-i", self.source, "-frames:v", "1", "-an", "-sn",
            "-vf", f"scale={THUMB_WIDTH}:-2:flags=bilinear", "-q:v", "4", "-y", str(tmp),
        ]
        if media.run(cmd, timeout=45) is None:     # nothing ran: shutting down
            tmp.unlink(missing_ok=True)
            return False
        if tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(self.target)
            return True
        tmp.unlink(missing_ok=True)
        return False


class ThumbCache(QObject):
    """Lazy thumbnail provider: returns a placeholder now, the real frame later."""

    ready = Signal(int)  # video_id

    def __init__(self, parent=None):
        super().__init__(parent)
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, QThreadPool.globalInstance().maxThreadCount() // 2)))
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        QPixmapCache.setCacheLimit(max(QPixmapCache.cacheLimit(), PICTURE_CACHE_KB))
        self._queued: set[int] = set()
        self._failed: set[int] = set()
        self._placeholder: QPixmap | None = None

    def placeholder(self, width: int, height: int) -> QPixmap:
        if self._placeholder is None or self._placeholder.size().width() != width:
            pixmap = QPixmap(width, height)
            gradient = QLinearGradient(0, 0, 0, height)
            gradient.setColorAt(0, QColor("#26272e"))
            gradient.setColorAt(1, QColor("#1b1c21"))
            painter = QPainter(pixmap)
            painter.fillRect(0, 0, width, height, gradient)
            painter.setPen(QColor("#3c3e47"))
            font = painter.font()
            font.setPointSize(max(10, height // 8))
            painter.setFont(font)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "▶")
            painter.end()
            self._placeholder = pixmap
        return self._placeholder

    def get(self, video: dict) -> QPixmap | None:
        """Cached pixmap for a video row, scheduling extraction on first miss."""
        video_id = video["id"]
        pixmap = QPixmapCache.find(_key(video_id))
        if pixmap is not None:
            return pixmap
        target = self.path_for(video)
        if target.exists():
            pixmap = QPixmap(str(target))
            if not pixmap.isNull():
                QPixmapCache.insert(_key(video_id), pixmap)
                return pixmap
            target.unlink(missing_ok=True)
        self.request(video)
        return None

    def path_for(self, video: dict) -> Path:
        return THUMB_DIR / f"{cache_key(video['path'], video['size'], video['mtime'])}.jpg"

    def request(self, video: dict) -> None:
        video_id = video["id"]
        if video_id in self._queued or video_id in self._failed:
            return
        self._queued.add(video_id)
        self._pool.start(
            _ThumbJob(video_id, video["path"], self.path_for(video), video.get("duration") or 0.0,
                      self._signals)
        )

    def forget(self, video: dict) -> None:
        QPixmapCache.remove(_key(video["id"]))
        self._failed.discard(video["id"])
        self.path_for(video).unlink(missing_ok=True)

    def _on_done(self, video_id: int, path: str) -> None:
        self._queued.discard(video_id)
        if not path:
            self._failed.add(video_id)
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._failed.add(video_id)
            return
        QPixmapCache.insert(_key(video_id), pixmap)
        self.ready.emit(video_id)

    def sweep(self, videos: list) -> int:
        """Throw away cached posters no video asks for any more."""
        from .covers import sweep                # noqa: PLC0415  (housekeeping)
        return sweep(THUMB_DIR, {self.path_for(video).name for video in videos})

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)
