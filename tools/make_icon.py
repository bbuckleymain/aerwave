"""Render the app icon to a PNG (used by install.sh for the desktop entry)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.argv = [sys.argv[0], "-platform", "offscreen"] + sys.argv[1:]

from PySide6.QtGui import QGuiApplication  # noqa: E402

from aerwave.icons import app_icon  # noqa: E402


def main() -> int:
    target = Path(sys.argv[-1])
    QGuiApplication(sys.argv[:3])
    size = 256
    if not app_icon(size).pixmap(size, size).save(str(target)):
        print(f"could not write {target}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
