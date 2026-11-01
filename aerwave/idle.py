"""Keep the screen awake while something is actually playing.

Desktops dim and blank the screen after a stretch with no keyboard or mouse
input — which is exactly what watching a film looks like. Media players ask the
session to hold that off. mpv normally does this itself, but only through the
window its own video output owns; Aerwave renders through the libmpv render API and
has no such window, so the request has to be made here.

Two D-Bus services are asked, because they cover different things:

* ``org.freedesktop.ScreenSaver`` (kwin, mutter, xfce4-screensaver…) — stops the
  screen blanking and locking;
* ``org.freedesktop.PowerManagement.Inhibit`` (powerdevil, xfce4-power-manager…)
  — stops dimming and automatic suspend.

Whichever exists answers, and anything missing is skipped. If neither is there,
``systemd-inhibit`` is used instead.

The hold is released by dropping a dedicated bus connection rather than by
calling ``UnInhibit``: that method takes a ``uint32`` which PySide6 cannot
marshal (the call is rejected with ``UnknownMethod``), while every one of these
services already releases a client's inhibitions when its connection goes away —
which is also what makes the screen recover if the app ever crashes.
"""

import itertools
import os
import subprocess

from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusConnection, QDBusInterface

APP_NAME = "Aerwave"

# (service, object path, interface)
TARGETS = [
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
     "org.freedesktop.ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/ScreenSaver",
     "org.freedesktop.ScreenSaver"),
    ("org.freedesktop.PowerManagement.Inhibit", "/org/freedesktop/PowerManagement/Inhibit",
     "org.freedesktop.PowerManagement.Inhibit"),
]

_counter = itertools.count(1)


class IdleInhibitor(QObject):
    """Holds "the user is not idle" for as long as it is active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connection_name = ""
        self._bus_name = ""          # the unique bus name holding the inhibitions
        self._granted: list[str] = []
        self._process: subprocess.Popen | None = None
        self._reason = ""

    @property
    def active(self) -> bool:
        return bool(self._granted) or self._process is not None

    @property
    def bus_name(self) -> str:
        """Unique D-Bus name the holds were taken on — for diagnostics."""
        return self._bus_name

    @property
    def held_by(self) -> list[str]:
        """Which services accepted the request — useful for diagnostics."""
        names = list(self._granted)
        if self._process is not None:
            names.append("systemd-inhibit")
        return names

    def set_active(self, active: bool, reason: str = "Playing media") -> None:
        if active:
            self.inhibit(reason)
        else:
            self.release()

    def inhibit(self, reason: str = "Playing media") -> None:
        if self.active:
            self._reason = reason
            return
        self._reason = reason
        self._connection_name = f"aerwave-idle-{os.getpid()}-{next(_counter)}"
        bus = QDBusConnection.connectToBus(QDBusConnection.BusType.SessionBus,
                                           self._connection_name)
        if bus.isConnected():
            self._bus_name = bus.baseService()
            claimed: set[str] = set()
            for service, path, interface_name in TARGETS:
                if service in claimed:
                    continue          # kwin answers on two paths; one hold is enough
                if self._ask(bus, service, path, interface_name, reason):
                    claimed.add(service)
                    self._granted.append(service)
        # Drop the local handle before releasing, so nothing keeps the
        # connection alive: Qt only closes it once the last reference is gone.
        del bus
        if not self._granted:
            QDBusConnection.disconnectFromBus(self._connection_name)
            self._connection_name = ""
            self._bus_name = ""
            self._start_fallback(reason)

    def _ask(self, bus, service: str, path: str, interface_name: str, reason: str) -> bool:
        interface = QDBusInterface(service, path, interface_name, bus)
        if not interface.isValid():
            return False
        reply = interface.call("Inhibit", APP_NAME, reason)
        arguments = reply.arguments() if reply is not None else []
        if not arguments:
            return False
        try:
            return int(arguments[0]) != 0
        except (TypeError, ValueError):
            return False

    def _start_fallback(self, reason: str) -> None:
        """No inhibit service answered — hold a logind lock instead."""
        try:
            self._process = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", f"--who={APP_NAME}",
                 f"--why={reason}", "--mode=block", "sleep", "infinity"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            self._process = None

    def release(self) -> None:
        if self._connection_name:
            # Dropping the connection is the release: the services watch for the
            # caller disappearing and clear its inhibitions.
            QDBusConnection.disconnectFromBus(self._connection_name)
            self._connection_name = ""
            self._bus_name = ""
        self._granted.clear()
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
            self._process = None
        self._reason = ""
