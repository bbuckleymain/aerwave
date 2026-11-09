"""Finding places to sync to, and explaining the ones that cannot be reached.

A phone plugged in over USB speaks MTP, which is not a filesystem. Something has
to translate it into a folder before any ordinary program can write to it:

* **gvfs**, with its MTP backend and FUSE daemon, mounts it under
  ``$XDG_RUNTIME_DIR/gvfs/mtp:host=…`` — a real path every application can use.
  How those are packaged differs by distribution, which is what INSTALL_HINTS is
  for;
* **KDE's KIO** (``kio-extras``) gives Dolphin an ``mtp://`` address instead.
  That works inside KDE's own file dialogs and nowhere else, which is why a
  phone can be visible in Dolphin and invisible to everything else.

So a phone that libmtp can see but which has no path is not a bug in Aerwave — the
translation layer simply is not installed.
"""

import os
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import media

MOUNT_HINTS = ("/run/media", "/media", "/mnt")
# Package names checked against each distribution's own index. Arch has no
# gvfs-fuse package at all — gvfsd-fuse ships inside gvfs itself — so naming it
# makes the whole command fail with "target not found". Anything not listed here
# gets the generic wording rather than a guess.
INSTALL_HINTS = {
    "arch": "sudo pacman -S gvfs gvfs-mtp",
    "debian": "sudo apt install gvfs-backends gvfs-fuse",
    "fedora": "sudo dnf install gvfs-mtp gvfs-fuse",
}


def gvfs_root() -> Path | None:
    """Where gvfs puts its mounts, if it is running at all."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    candidates = [Path(runtime) / "gvfs", Path.home() / ".gvfs"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def gvfs_available() -> bool:
    """Is the FUSE bridge that turns MTP into a folder installed?"""
    return shutil.which("gvfsd-fuse") is not None or gvfs_root() is not None


def install_hint() -> str:
    """The command that would make phones appear as folders on this system."""
    family = ""
    try:
        if Path("/etc/os-release").is_file():
            data = dict(
                line.split("=", 1)
                for line in Path("/etc/os-release").read_text().splitlines()
                if "=" in line)
            family = (data.get("ID_LIKE") or data.get("ID") or "").strip('"').lower()
    except OSError:
        pass
    for key, command in INSTALL_HINTS.items():
        if key in family:
            return command
    return "install your distribution's gvfs MTP and gvfs FUSE packages"


def mounted_places() -> list[str]:
    """Drives, virtual disks and any gvfs mounts that exist right now."""
    found: list[str] = []
    for base in MOUNT_HINTS:
        root = Path(base)
        if not root.is_dir():
            continue
        for level in (root, *[p for p in root.iterdir() if p.is_dir()]):
            try:
                for entry in level.iterdir():
                    if entry.is_dir() and entry.is_mount():
                        found.append(str(entry))
            except (OSError, PermissionError):
                continue
    root = gvfs_root()
    if root is not None:
        try:
            found.extend(str(p) for p in root.iterdir() if p.is_dir())
        except OSError:
            pass
    return sorted(dict.fromkeys(found))


def parse_mtp_devices(output: str) -> list[str]:
    """Device names out of mtp-detect's very chatty output.

    The whole listing is considered at once: libmtp prints the friendly name
    (what the owner called the phone) *after* the manufacturer and model, and
    prints the model twice, so reading line by line picks the wrong one.
    """
    def usable(value: str) -> bool:
        return bool(value) and value.lower() not in ("(null)", "?", "none")

    friendly = [m.strip().strip('"') for m in
                re.findall(r'[Ff]riendly [Nn]ame:\s*"?([^"\n]+)"?', output)]
    friendly = [name for name in friendly if usable(name)]
    if friendly:
        return list(dict.fromkeys(friendly))

    vendors = [m.strip() for m in re.findall(r"^\s*Manufacturer:\s*(.+)$", output, re.M)]
    models = [m.strip() for m in re.findall(r"^\s*Model:\s*(.+)$", output, re.M)]
    names = []
    for vendor, model in zip(vendors, models):
        name = " ".join(part for part in (vendor, model) if usable(part)).strip()
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def detect_mtp_devices(timeout: int = 25) -> list[str]:
    """Phones libmtp can see. Slow — it enumerates USB, so call it off the GUI thread."""
    if shutil.which("mtp-detect") is None:
        return []
    result = media.run(["mtp-detect"], timeout=timeout)
    if result is None:                        # nothing ran: we are shutting down
        return []
    combined = (result.stdout or "") + (result.stderr or "")
    if "No raw devices found" in combined:
        return []
    return parse_mtp_devices(combined)


class _Signals(QObject):
    done = Signal(list, list, bool, str)   # mounts, phone names, gvfs present, hint


class _Probe(QRunnable):
    def __init__(self, signals: _Signals):
        super().__init__()
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        self.signals.done.emit(mounted_places(), detect_mtp_devices(),
                               gvfs_available(), install_hint())


class DeviceFinder(QObject):
    """Looks for somewhere to sync to, without blocking the interface."""

    found = Signal(list, list, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _Signals(self)
        self._signals.done.connect(self._clear_busy)
        self._signals.done.connect(self.found)
        self._busy = False

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._pool.start(_Probe(self._signals))

    def _clear_busy(self, *_args) -> None:
        self._busy = False

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(1000)
