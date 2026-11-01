#!/usr/bin/env python3
"""Pack the tree into one self-contained installer.

The result is `dist/install-aerwave.sh`: the shell script that does the
installing, with a gzipped tar of the source base64'd onto the end of it after
a marker line. The script finds the marker, decodes what follows, checks it
against a hash written into its own header, and unpacks it.

One file is the whole point. There is nothing to clone, nothing to `pip`, and
the same file installs, updates and uninstalls, so a copy kept on a USB stick
is still a working installer a year later.

    python tools/make_installer.py

The shell half lives in `tools/installer.sh`, which is the script exactly as it
ships apart from two lines — the version and the payload hash — that are filled
in here, because neither can be known until the payload exists.
"""

import base64
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "tools" / "installer.sh"
MARKER = "__AERWAVE_PAYLOAD_BELOW__"

# What a working install needs, and nothing else: no caches, no build output,
# no editor leftovers, no installer built by a previous run.
INCLUDE = ("aerwave", "docs", "tools/make_icon.py", "aerwave.desktop.in",
           "pyproject.toml", "requirements.txt", "README.md")
SKIP_DIRS = {"__pycache__", ".git", "dist", "build", ".venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".swp", ".orig", ".rej"}


def version() -> str:
    text = (ROOT / "aerwave" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not found:
        raise SystemExit("aerwave/__init__.py has no __version__")
    return found.group(1)


def wanted(path: Path) -> bool:
    if any(part in SKIP_DIRS or part.startswith(".") for part in path.parts):
        return False
    return path.suffix not in SKIP_SUFFIXES


def payload(release: str) -> bytes:
    """A gzipped tar of the source, with every file under `aerwave-<version>/`."""
    buffer = io.BytesIO()
    # mtime fixed and names sorted, so building the same source twice gives
    # the same bytes and `--status` can be trusted to mean something.
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT,
                      compresslevel=9) as archive:
        for entry in INCLUDE:
            source = ROOT / entry
            if not source.exists():
                raise SystemExit(f"{entry} is missing — the installer needs it")
            paths = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in [source, *paths] if source.is_dir() else paths:
                relative = path.relative_to(ROOT)
                if not wanted(relative) or path.is_symlink():
                    continue
                info = archive.gettarinfo(str(path), f"{release}/{relative}")
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = "aerwave"
                info.mode = 0o755 if path.is_dir() else 0o644
                if path.is_dir():
                    archive.addfile(info)
                else:
                    with open(path, "rb") as handle:
                        archive.addfile(info, handle)
    return buffer.getvalue()


def wrap(blob: bytes, width: int = 76) -> str:
    text = base64.b64encode(blob).decode("ascii")
    return "\n".join(text[at:at + width] for at in range(0, len(text), width)) + "\n"


def verify(script: Path, release: str) -> None:
    """Unpack what was just written, and check it is what went in."""
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run(["bash", str(script), "--extract-to", scratch],
                       check=True, capture_output=True, text=True)
        unpacked = Path(scratch) / release
        for entry in ("aerwave/__init__.py", "aerwave/book_files.py", "aerwave.desktop.in"):
            if not (unpacked / entry).is_file():
                raise SystemExit(f"the payload is missing {entry}")
        for path in sorted((ROOT / "aerwave").glob("*.py")):
            mirrored = unpacked / "aerwave" / path.name
            if mirrored.read_bytes() != path.read_bytes():
                raise SystemExit(f"{path.name} did not survive the round trip")


def main() -> int:
    release = f"aerwave-{version()}"
    if not TEMPLATE.is_file():
        raise SystemExit(f"{TEMPLATE} is missing")
    blob = payload(release)
    digest = hashlib.sha256(blob).hexdigest()

    header = TEMPLATE.read_text(encoding="utf-8")
    # The marker as a line of its own: the word also appears inside the script,
    # in the grep that looks for this very line, and cutting there would leave
    # half a command behind.
    cut = re.search(rf"^{MARKER}$", header, flags=re.M)
    if cut is None:
        raise SystemExit(f"{TEMPLATE} has no {MARKER} line of its own")
    header = header[:cut.end()] + "\n"
    header = re.sub(r'^VERSION="[^"]*"', f'VERSION="{version()}"', header, count=1,
                    flags=re.M)
    header = re.sub(r'^PAYLOAD_SHA256="[^"]*"', f'PAYLOAD_SHA256="{digest}"',
                    header, count=1, flags=re.M)
    header = re.sub(r"^# Aerwave [\d.]+ —", f"# Aerwave {version()} —", header, count=1,
                    flags=re.M)

    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    script = out / "install-aerwave.sh"
    script.write_text(header + wrap(blob), encoding="utf-8")
    script.chmod(0o755)
    verify(script, release)
    stamped = out / f"install-{release}.sh"
    shutil.copy2(script, stamped)
    stamped.chmod(0o755)

    size = script.stat().st_size
    print(f"{script.relative_to(ROOT)}  {size / 1024:.0f} KB  "
          f"({len(blob) / 1024:.0f} KB packed)  version {version()}")
    print(f"{stamped.relative_to(ROOT)}")
    print(f"payload sha256 {digest}")
    print(f"built {time.strftime('%Y-%m-%d %H:%M')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
