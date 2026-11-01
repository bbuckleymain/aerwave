"""Entry point: python -m aeris"""

import signal
import socket
import sys

from PySide6.QtCore import QSocketNotifier
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__, style
from .icons import app_icon, write_chevron, write_tick
from .mpvwidget import use_c_numeric_locale
from .paths import ensure_dirs


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0

    ensure_dirs()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setDesktopFileName("aeris")
    QApplication.setOrganizationName("Aeris")

    app = QApplication(argv)
    # Qt resets LC_NUMERIC when it starts; libmpv needs it back on "C".
    use_c_numeric_locale()
    # The theme and how see-through the window is both live in the settings
    # table, and both are baked into the stylesheet, so they have to be read
    # before anything paints.
    from .db import Library
    from .widgets import chosen_glass
    settings = Library()
    style.set_theme(settings.get("theme", style.DEFAULT_THEME))
    style.set_glass(chosen_glass(settings))
    settings.close()
    app.setStyleSheet(style.stylesheet(write_chevron(style.MUTED),
                                       write_tick(style.ON_ACCENT)))
    app.setWindowIcon(app_icon(256))

    from .mainwindow import MainWindow

    window = MainWindow()
    window.show()

    # Ctrl+C in a terminal, and the SIGTERM a logout sends, both have to reach
    # the window rather than kill the process where it stands — closing it is
    # what saves your place in whatever is playing.
    #
    # Qt's event loop sits in poll() and never runs Python, so a plain handler
    # would not be seen until the next stray event. Writing the signal down a
    # socket wakes the loop at once, and costs nothing while nothing happens —
    # where the usual trick, a timer ticking away for ever in case a signal
    # arrives, costs a wakeup several times a second for the life of the app.
    waker, listener = socket.socketpair()
    waker.setblocking(False)
    listener.setblocking(False)
    signal.set_wakeup_fd(waker.fileno())
    notifier = QSocketNotifier(listener.fileno(), QSocketNotifier.Type.Read)

    def _signalled(*_args) -> None:
        try:
            listener.recv(64)
        except OSError:
            pass
        window.close()

    notifier.activated.connect(_signalled)
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: None)   # the socket is the wake-up

    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        window.open_external(files[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
