"""MPRIS2, so the desktop can drive playback.

Registering ``org.mpris.MediaPlayer2.aerwave`` on the session bus is what makes the
keyboard's media keys work, puts Aerwave in Plasma's media applet (with cover art
and a scrub bar) and lets the lock screen pause playback.

The controller is duck-typed — anything providing the ``mpris_*`` methods below
can be driven. :class:`~aerwave.mainwindow.MainWindow` implements them and forwards
to whichever player is on screen.
"""

from PySide6.QtCore import ClassInfo, Property, QMetaType, QObject, Signal, Slot
from PySide6.QtDBus import (QDBusAbstractAdaptor, QDBusArgument, QDBusConnection,
                            QDBusMessage, QDBusObjectPath)

SERVICE_NAME = "org.mpris.MediaPlayer2.aerwave"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

MIME_TYPES = [
    "video/mp4", "video/x-matroska", "video/webm", "video/quicktime",
    "video/x-msvideo", "video/mpeg", "audio/mp4", "audio/mpeg", "audio/flac",
    "audio/ogg", "audio/x-m4b",
]


def _no_names() -> QDBusArgument:
    """An empty list of property names, typed as one.

    PropertiesChanged ends with the names of properties that changed without
    their new values being sent — always none, here. Handed a bare `[]`,
    PySide cannot tell what the list would have held and sends an array of
    variants, so the signal goes out as ``sa{sv}av`` instead of the spec's
    ``sa{sv}as``. GLib clients (playerctld, scrobblers) shrug that off, but
    Qt matches signals on their exact signature: Plasma never received a
    single update from Aerwave, believed whatever it saw when Aerwave first
    appeared, and so never noticed it start playing — which is why media keys
    went to the browser instead. An explicitly typed empty array fixes it.
    """
    names = QDBusArgument()
    names.beginArray(QMetaType(QMetaType.Type.QString))
    names.endArray()
    return names


@ClassInfo({"D-Bus Interface": ROOT_INTERFACE})
class _RootAdaptor(QDBusAbstractAdaptor):
    """org.mpris.MediaPlayer2 — what the application itself can do."""

    def __init__(self, service: "MprisService"):
        super().__init__(service)
        self._service = service

    @Property(bool)
    def CanQuit(self) -> bool:
        return True

    @Property(bool)
    def CanRaise(self) -> bool:
        return True

    @Property(bool)
    def HasTrackList(self) -> bool:
        return False

    @Property(str)
    def Identity(self) -> str:
        return "Aerwave"

    @Property(str)
    def DesktopEntry(self) -> str:
        return "aerwave"

    @Property("QStringList")
    def SupportedUriSchemes(self) -> list:
        return ["file"]

    @Property("QStringList")
    def SupportedMimeTypes(self) -> list:
        return MIME_TYPES

    @Slot()
    def Raise(self) -> None:
        self._service.controller.mpris_raise()

    @Slot()
    def Quit(self) -> None:
        self._service.controller.mpris_quit()


@ClassInfo({"D-Bus Interface": PLAYER_INTERFACE})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    """org.mpris.MediaPlayer2.Player — transport and now-playing."""

    Seeked = Signal("qlonglong")

    def __init__(self, service: "MprisService"):
        super().__init__(service)
        self._service = service
        # Emitting Seeked here sends it on the bus with its declared type,
        # int64, which is what the spec asks for and what Qt-based clients
        # insist on. A hand-built message picked int32 for any position under
        # about 36 minutes, and Plasma dropped every one of them.
        self.setAutoRelaySignals(True)

    # ------------------------------------------------------------ properties

    @Property(str)
    def PlaybackStatus(self) -> str:
        return self._service.controller.mpris_status()

    @Property("QVariantMap")
    def Metadata(self) -> dict:
        return self._service.metadata()

    @Property(float)
    def Rate(self) -> float:
        return float(self._service.controller.mpris_rate())

    @Rate.setter
    def Rate(self, value: float) -> None:
        self._service.controller.mpris_set_rate(float(value))

    @Property(float)
    def Volume(self) -> float:
        return float(self._service.controller.mpris_volume())

    @Volume.setter
    def Volume(self, value: float) -> None:
        self._service.controller.mpris_set_volume(float(value))

    @Property("qlonglong")
    def Position(self) -> int:
        return int(self._service.controller.mpris_position() * 1_000_000)

    @Property(float)
    def MinimumRate(self) -> float:
        return 0.25

    @Property(float)
    def MaximumRate(self) -> float:
        return 4.0

    @Property(bool)
    def CanGoNext(self) -> bool:
        return bool(self._service.controller.mpris_can_go_next())

    @Property(bool)
    def CanGoPrevious(self) -> bool:
        return bool(self._service.controller.mpris_can_go_previous())

    @Property(bool)
    def CanPlay(self) -> bool:
        return self._service.controller.mpris_status() != "Stopped"

    @Property(bool)
    def CanPause(self) -> bool:
        return self._service.controller.mpris_status() != "Stopped"

    @Property(bool)
    def CanSeek(self) -> bool:
        return self._service.controller.mpris_status() != "Stopped"

    @Property(bool)
    def CanControl(self) -> bool:
        return True

    # --------------------------------------------------------------- methods

    @Slot()
    def Play(self) -> None:
        self._service.controller.mpris_play()

    @Slot()
    def Pause(self) -> None:
        self._service.controller.mpris_pause()

    @Slot()
    def PlayPause(self) -> None:
        self._service.controller.mpris_play_pause()

    @Slot()
    def Stop(self) -> None:
        self._service.controller.mpris_stop()

    @Slot()
    def Next(self) -> None:
        self._service.controller.mpris_next()

    @Slot()
    def Previous(self) -> None:
        self._service.controller.mpris_previous()

    @Slot("qlonglong")
    def Seek(self, offset: int) -> None:
        self._service.controller.mpris_seek(offset / 1_000_000)

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, _track: QDBusObjectPath, position: int) -> None:
        self._service.controller.mpris_set_position(position / 1_000_000)

    @Slot(str)
    def OpenUri(self, uri: str) -> None:
        self._service.controller.mpris_open_uri(uri)


class MprisService(QObject):
    """Owns the D-Bus registration and pushes state changes out to the desktop."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._registered = False
        self._last: dict = {}
        self._track_serial = 0
        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)

    # ------------------------------------------------------------ life cycle

    def register(self) -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        if not bus.registerObject(
            OBJECT_PATH, self,
            QDBusConnection.RegisterOption.ExportAdaptors,
        ):
            return False
        if not bus.registerService(SERVICE_NAME):
            return False
        self._registered = True
        self.refresh(force=True)
        return True

    def unregister(self) -> None:
        if not self._registered:
            return
        bus = QDBusConnection.sessionBus()
        bus.unregisterService(SERVICE_NAME)
        bus.unregisterObject(OBJECT_PATH)
        self._registered = False

    # -------------------------------------------------------------- metadata

    def metadata(self) -> dict:
        raw = self.controller.mpris_metadata() or {}
        if not raw:
            return {"mpris:trackid": QDBusObjectPath(NO_TRACK)}
        data = {
            "mpris:trackid": QDBusObjectPath(
                f"/org/mpris/MediaPlayer2/aerwave/track/{self._track_serial}"),
            "xesam:title": str(raw.get("title") or "Aerwave"),
        }
        length = int(float(raw.get("length") or 0) * 1_000_000)
        if length > 0:
            # Microseconds. Without this no client can draw a scrub bar,
            # which is most of the point of announcing ourselves at all.
            #
            # The spec says int64. PySide picks the D-Bus type from the value,
            # so a long book goes out as one and a three-minute song as int32;
            # both are read correctly by every client tried here, and there is
            # no way to pin the type down from Python without a second D-Bus
            # binding.
            data["mpris:length"] = length
        artist = raw.get("artist")
        if artist:
            data["xesam:artist"] = [str(artist)]
        album = raw.get("album")
        if album:
            data["xesam:album"] = str(album)
        art = raw.get("art_url")
        if art:
            data["mpris:artUrl"] = str(art)
        url = raw.get("url")
        if url:
            data["xesam:url"] = str(url)
        return data

    # --------------------------------------------------------------- updates

    def track_changed(self) -> None:
        """Call when a different file starts, so clients replace their metadata."""
        self._track_serial += 1
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        """Emit PropertiesChanged for anything that moved since last time."""
        if not self._registered:
            return
        current = {
            "PlaybackStatus": self.controller.mpris_status(),
            "Metadata": self.metadata(),
            "Volume": float(self.controller.mpris_volume()),
            "Rate": float(self.controller.mpris_rate()),
            "CanGoNext": bool(self.controller.mpris_can_go_next()),
            "CanGoPrevious": bool(self.controller.mpris_can_go_previous()),
            "CanPlay": self.controller.mpris_status() != "Stopped",
            "CanPause": self.controller.mpris_status() != "Stopped",
            "CanSeek": self.controller.mpris_status() != "Stopped",
        }
        changed = {key: value for key, value in current.items()
                   if force or self._last.get(key) != value}
        self._last = current
        if not changed:
            return
        message = QDBusMessage.createSignal(OBJECT_PATH, PROPERTIES_INTERFACE,
                                            "PropertiesChanged")
        message.setArguments([PLAYER_INTERFACE, changed, _no_names()])
        QDBusConnection.sessionBus().send(message)

    def seeked(self, position_seconds: float) -> None:
        """Tell clients the position jumped, so their scrub bars follow."""
        if not self._registered:
            return
        self._player.Seeked.emit(int(position_seconds * 1_000_000))
