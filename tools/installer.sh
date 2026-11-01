#!/usr/bin/env bash
#
# Aerwave 2.0.0 — installer and updater.
#
# One self-contained file: the application is packed into the bottom of this
# script. Run it to install Aerwave; run a newer copy later to update in place.
# Your library and watch history are never touched by an update.
#
#   ./install-aerwave.sh              install, or update an existing install
#   ./install-aerwave.sh --status     show what is bundled here vs installed
#   ./install-aerwave.sh --uninstall  remove the app (keeps your library)
#   ./install-aerwave.sh --help       every option
#
set -euo pipefail

VERSION="2.0.0"
PAYLOAD_SHA256="e9197e3142d4f621bf3cd58f0c3cf196da0665028be7a1d6b24290326a3d631b"

PREFIX="${AERWAVE_PREFIX:-$HOME/.local/lib/aerwave}"
BIN_DIR="${AERWAVE_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/aerwave"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/aerwave"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
PYTHON_BIN="${PYTHON:-python3}"

ACTION="install"
EXTRACT_TO=""
FORCE=0
PURGE=0
DESKTOP=1
QUIET=0

BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
fi

say()  { (( QUIET )) || printf '%s\n' "$*"; }
step() { (( QUIET )) || printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
warn() { printf '%s!%s   %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
Aerwave $VERSION — installer and updater

Usage: $(basename "$0") [options]

Actions:
  (no action)          Install Aerwave, or update an existing install to $VERSION
  --status             Show the bundled and installed versions, then exit
  --uninstall          Remove the app, launcher and menu entry
  --purge              With --uninstall, also delete your library and thumbnails
  --version            Print the version bundled in this file
  --extract-to DIR     Unpack the bundled source into DIR and stop
  --help               Show this message

Options:
  --force              Reinstall even if $VERSION is already installed
  --prefix DIR         Install the app here          (default: $PREFIX)
  --bin-dir DIR        Put the launcher here         (default: $BIN_DIR)
  --no-desktop         Skip the menu entry and icon
  --python PATH        Python to build the venv with (default: $PYTHON_BIN)
  --quiet              Only print warnings and errors

Environment: AERWAVE_PREFIX, AERWAVE_BIN_DIR, PYTHON are honoured as defaults.

Your library lives in $DATA_DIR and is left alone unless you pass --purge.
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)     ACTION="status" ;;
        --uninstall)  ACTION="uninstall" ;;
        --purge)      PURGE=1 ;;
        --force|--reinstall) FORCE=1 ;;
        --no-desktop) DESKTOP=0 ;;
        --quiet|-q)   QUIET=1 ;;
        --prefix)     PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --bin-dir)    BIN_DIR="${2:?--bin-dir needs a directory}"; shift ;;
        --python)     PYTHON_BIN="${2:?--python needs a path}"; shift ;;
        --prefix=*)   PREFIX="${1#*=}" ;;
        --bin-dir=*)  BIN_DIR="${1#*=}" ;;
        --python=*)   PYTHON_BIN="${1#*=}" ;;
        --extract-to) ACTION="extract"; EXTRACT_TO="${2:?--extract-to needs a directory}"; shift ;;
        --extract-to=*) ACTION="extract"; EXTRACT_TO="${1#*=}" ;;
        --version|-V) printf 'Aerwave %s\n' "$VERSION"; exit 0 ;;
        --help|-h)    usage; exit 0 ;;
        *)            die "unknown option: $1  (try --help)" ;;
    esac
    shift
done

WORK_DIR=""
# The self-tests import every module, and importing the app is what carries a
# library over from its old name. They run with a throwaway home for data, so
# installing never moves, creates or touches anyone's library: that happens
# the first time the app itself is opened.
# Made here rather than on first use: the tests run inside $( ), and a folder
# made in there would never be seen — or cleaned up — out here.
SANDBOX="$(mktemp -d)"
sandboxed() {
    XDG_DATA_HOME="$SANDBOX" XDG_CACHE_HOME="$SANDBOX" XDG_CONFIG_HOME="$SANDBOX" "$@"
}
cleanup() {
    # Must end successfully: an EXIT trap's status becomes the script's status.
    [[ -n "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
    [[ -n "$SANDBOX" ]] && rm -rf "$SANDBOX"
    return 0
}
trap cleanup EXIT

# Not "${BASH_SOURCE[0]}": under `set -u` that aborts before the message below
# can explain itself when the script is piped in rather than saved to a file.
SELF="${BASH_SOURCE[0]:-}"
[[ -n "$SELF" && -r "$SELF" ]] || die "this installer carries the application inside itself,
so it has to be run from a saved file rather than a pipe.

  curl -LO <url>/install-aerwave.sh
  bash install-aerwave.sh"
SELF="$(cd "$(dirname "$SELF")" && pwd)/$(basename "$SELF")"

VENV="$PREFIX/.venv"
SRC="$PREFIX/src"
STAMP="$PREFIX/VERSION"
LAUNCHER="$BIN_DIR/aerwave"

installed_version() {
    [[ -r "$STAMP" ]] && tr -d '[:space:]' < "$STAMP" || true
}

# ---------------------------------------------------------------- inspection

show_status() {
    local current
    current="$(installed_version)"
    say "${BOLD}Aerwave${RESET}"
    say "  bundled in this file : $VERSION"
    if [[ -n "$current" ]]; then
        say "  installed            : $current"
        say "  location             : $PREFIX"
        say "  launcher             : $LAUNCHER$([[ -x $LAUNCHER ]] || echo '  (missing)')"
    elif [[ -x "$LAUNCHER" ]]; then
        say "  installed            : ${DIM}not by this installer${RESET}"
        say "  launcher             : $LAUNCHER ${DIM}(points elsewhere)${RESET}"
    else
        say "  installed            : ${DIM}not installed${RESET}"
    fi
    if [[ -f "$DATA_DIR/library.db" ]]; then
        local count py="$VENV/bin/python"
        [[ -x "$py" ]] || py="$PYTHON_BIN"
        count="$("$py" - "$DATA_DIR/library.db" 2>/dev/null <<'PY' || echo "?"
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    print(con.execute("select count(*) from videos where missing=0").fetchone()[0])
except Exception:
    print("?")
PY
)"
        if [[ "$count" == "?" ]]; then
            say "  library              : present in $DATA_DIR"
        else
            say "  library              : $count videos in $DATA_DIR"
        fi
    else
        say "  library              : ${DIM}empty${RESET}"
    fi
}

# ------------------------------------------------------------- prerequisites

package_hint() {
    local id=""
    [[ -r /etc/os-release ]] && id="$(. /etc/os-release 2>/dev/null && echo "${ID_LIKE:-$ID}")"
    case "$id" in
        *arch*)   echo "sudo pacman -S --needed mpv ffmpeg" ;;
        *debian*|*ubuntu*) echo "sudo apt install libmpv2 ffmpeg" ;;
        *fedora*|*rhel*)   echo "sudo dnf install mpv-libs ffmpeg" ;;
        *suse*)   echo "sudo zypper install libmpv2 ffmpeg" ;;
        *)        echo "install your distribution's libmpv and ffmpeg packages" ;;
    esac
}

check_prerequisites() {
    step "Checking prerequisites"
    command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found. Install Python 3.10 or newer."
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        || die "Python 3.10 or newer is required ($("$PYTHON_BIN" -V 2>&1))."
    "$PYTHON_BIN" -c 'import venv' 2>/dev/null \
        || die "the venv module is missing. On Debian/Ubuntu: sudo apt install python3-venv"

    local missing=()
    "$PYTHON_BIN" - <<'PY' || missing+=("libmpv")
import ctypes.util, sys
sys.exit(0 if ctypes.util.find_library("mpv") else 1)
PY
    command -v ffmpeg  >/dev/null || missing+=("ffmpeg")
    command -v ffprobe >/dev/null || missing+=("ffprobe")
    if (( ${#missing[@]} )); then
        printf '%serror:%s missing system packages: %s\n' "$RED" "$RESET" "${missing[*]}" >&2
        printf '       %s\n' "$(package_hint)" >&2
        exit 1
    fi
    say "    Python $("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'), libmpv, ffmpeg — all present"
}

# ------------------------------------------------------------------ payload

extract_payload() {
    local dest="$1" start archive
    start="$(grep -n '^__AERWAVE_PAYLOAD_BELOW__$' "$SELF" | head -1 | cut -d: -f1)"
    [[ -n "$start" ]] || die "this file has no payload — it may have been truncated."
    archive="$dest/aerwave.tar.gz"
    tail -n +"$((start + 1))" "$SELF" | base64 -d > "$archive" 2>/dev/null \
        || die "could not decode the payload — the file is damaged."

    if command -v sha256sum >/dev/null; then
        local actual
        actual="$(sha256sum "$archive" | cut -d' ' -f1)"
        [[ "$actual" == "$PAYLOAD_SHA256" ]] \
            || die "payload checksum mismatch — this file is corrupt.
  expected $PAYLOAD_SHA256
  got      $actual"
    else
        warn "sha256sum not found; skipping the integrity check."
    fi
    tar -xzf "$archive" -C "$dest" || die "could not unpack the payload."
    rm -f "$archive"
    local unpacked
    unpacked="$(find "$dest" -maxdepth 1 -mindepth 1 -type d | head -1)"
    [[ -n "$unpacked" ]] || die "the payload unpacked to nothing."
    printf '%s' "$unpacked"
}

# --------------------------------------------------------------- verifying

VERIFY_OUTPUT=""

verify_install() {
    # Import every module and load libmpv, so a half-written install is caught
    # here rather than the first time the window is opened. -I keeps the current
    # directory off sys.path, so this checks the copy we installed and not some
    # other "aerwave" that happens to be nearby.
    VERIFY_OUTPUT=""
    [[ -x "$VENV/bin/python" && -x "$VENV/bin/aerwave" ]] || {
        VERIFY_OUTPUT="the virtualenv is missing"
        return 1
    }
    VERIFY_OUTPUT="$(cd / && sandboxed "$VENV/bin/python" -I - 2>&1 <<'CHECK'
import importlib
import pkgutil

import aerwave

# Enumerate the package rather than listing modules, so this check cannot go
# stale as the app grows.
for module in pkgutil.iter_modules(aerwave.__path__):
    importlib.import_module(f"aerwave.{module.name}")
print(aerwave.__version__)
CHECK
)" || return 1
    [[ "$VERIFY_OUTPUT" == "$VERSION" ]] || return 1
    sandboxed "$VENV/bin/aerwave" --version >/dev/null 2>&1 || {
        VERIFY_OUTPUT="the launcher script in the virtualenv does not run"
        return 1
    }
    return 0
}

# ------------------------------------------------------------------ install

do_install() {
    local current
    current="$(installed_version)"
    if [[ -n "$current" && "$current" == "$VERSION" && $FORCE -eq 0 ]]; then
        if verify_install; then
            say "Aerwave $VERSION is already installed at $PREFIX and working."
            say "${DIM}Pass --force to reinstall it anyway.${RESET}"
            return 0
        fi
        warn "the installed copy of $VERSION is damaged — repairing it."
    fi
    if [[ -n "$current" ]]; then
        if [[ "$current" == "$VERSION" ]]; then
            step "Reinstalling Aerwave $VERSION"
        elif [[ "$(printf '%s\n%s\n' "$current" "$VERSION" | sort -V | tail -1)" == "$VERSION" ]]; then
            step "Updating Aerwave $current → $VERSION"
        else
            step "Rolling Aerwave back $current → $VERSION"
        fi
    else
        step "Installing Aerwave $VERSION"
    fi

    check_prerequisites

    if pgrep -f "$VENV/bin/aerwave" >/dev/null 2>&1; then
        warn "Aerwave is running — restart it after this finishes to pick up the new version."
    fi

    local unpacked
    WORK_DIR="$(mktemp -d)"
    step "Unpacking"
    unpacked="$(extract_payload "$WORK_DIR")"

    step "Installing to $PREFIX"
    mkdir -p "$PREFIX"
    rm -rf "$SRC"
    mv "$unpacked" "$SRC"

    if [[ ! -x "$VENV/bin/python" ]]; then
        say "    Creating the virtualenv (this downloads Qt, so it takes a minute)"
        "$PYTHON_BIN" -m venv "$VENV" || die "could not create the virtualenv at $VENV"
        "$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel \
            || die "could not download the Python build tools.
A first-time install needs an internet connection to fetch Qt (PySide6) from
PyPI — roughly 150 MB. Later updates reuse what is already here and work offline."
    else
        say "    Reusing the existing virtualenv"
    fi

    local pip_args=(--quiet)
    if "$VENV/bin/python" -c 'import setuptools' 2>/dev/null; then
        # Build against the venv's own setuptools so updates work offline.
        pip_args+=(--no-build-isolation)
    fi
    # First pass resolves dependencies (a no-op once they are satisfied)...
    "$VENV/bin/python" -m pip install "${pip_args[@]}" --upgrade "$SRC" \
        || die "installing the dependencies failed.
A first-time install downloads Qt (PySide6) from PyPI, so it needs an internet
connection. If you are online, the pip output above says what went wrong."
    # ...and the second forces this exact build of the app in, so that
    # reinstalls and rollbacks to an older version actually take effect.
    "$VENV/bin/python" -m pip install "${pip_args[@]}" --force-reinstall --no-deps "$SRC" \
        || die "installing the application failed. Re-run with --force, or check the output above."

    step "Verifying"
    local reported
    # Import every module and load libmpv, so a half-written install is caught
    # here rather than the first time the window is opened.
    # -I keeps the current directory off sys.path, so this checks the copy we
    # just installed and not some other "aerwave" that happens to be nearby.
    reported="$(cd / && sandboxed "$VENV/bin/python" -I - 2>&1 <<'CHECK'
import importlib, pkgutil

import aerwave

for module in pkgutil.iter_modules(aerwave.__path__):
    importlib.import_module(f"aerwave.{module.name}")
print(aerwave.__version__)
CHECK
)" || die "the installed app does not load:
$reported"
    [[ "$reported" == "$VERSION" ]] \
        || die "expected version $VERSION but the installed app reports: ${reported##*$'\n'}
The install did not take effect. Re-run with --force."
    sandboxed "$VENV/bin/aerwave" --version >/dev/null 2>&1 \
        || die "the launcher script in the virtualenv does not run."
    say "    every module loads, libmpv is reachable, version $reported"
    printf '%s\n' "$VERSION" > "$STAMP"

    step "Installing the launcher"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<LAUNCHER
#!/usr/bin/env bash
# Installed by the Aerwave installer. Safe to delete along with $PREFIX.
exec "$VENV/bin/aerwave" "\$@"
LAUNCHER
    chmod +x "$LAUNCHER"

    if (( DESKTOP )); then
        step "Adding the menu entry"
        mkdir -p "$APPS_DIR" "$ICON_DIR"
        if [[ -r "$SRC/aerwave.desktop.in" ]]; then
            sed "s|@EXEC@|$LAUNCHER|" "$SRC/aerwave.desktop.in" > "$APPS_DIR/aerwave.desktop"
        fi
        "$VENV/bin/python" "$SRC/tools/make_icon.py" "$ICON_DIR/aerwave.png" >/dev/null 2>&1 \
            || warn "could not render the icon; the menu entry will use a placeholder."
        command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
        command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
        command -v gtk-update-icon-cache >/dev/null \
            && gtk-update-icon-cache -qtf "${ICON_DIR%/256x256/apps}" 2>/dev/null || true
    fi

    retire_aeris

    say ""
    say "${GREEN}Aerwave $VERSION is installed.${RESET}"
    if (( DESKTOP )); then
        say "  Launch it from your application menu, or run: ${BOLD}aerwave${RESET}"
    else
        say "  Run it with: ${BOLD}$LAUNCHER${RESET}"
    fi
    say "  Library and watch history: $DATA_DIR"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) say ""
           if (( DESKTOP )); then
               say "  ${YELLOW}$BIN_DIR is not on your PATH${RESET} — the menu entry still works."
               say "  To use the 'aerwave' command too, add it to your shell profile."
           else
               say "  ${YELLOW}$BIN_DIR is not on your PATH${RESET} — call the launcher by its full path,"
               say "  or add that directory to your shell profile."
           fi ;;
    esac
}

# ------------------------------------------------------------ the old name

retire_aeris() {
    # Aerwave was called Aeris. Its install is replaced, not kept alongside:
    # two players in the menu, one of them stale, helps nobody. Only what the
    # Aeris installer itself put down goes — each piece is checked for its
    # marks first — and never the library, which the app carries over to its
    # new name when it is first opened.
    local old_prefix="${PREFIX%/*}/aeris" old_launcher="$BIN_DIR/aeris"
    local old_entry="$APPS_DIR/aeris.desktop" removed=0
    if [[ -x "$old_prefix/.venv/bin/aeris" ]] \
            && pgrep -f "$old_prefix/.venv/bin/aeris" >/dev/null 2>&1; then
        warn "Aeris is still running, so its old install was left in place."
        warn "Quit it and run this installer again to tidy it away."
        return 0
    fi
    if [[ -f "$old_launcher" ]] && grep -q "Installed by the Aeris installer" "$old_launcher"; then
        rm -f "$old_launcher"; removed=1
    fi
    if [[ -f "$old_entry" ]] && grep -q "^Name=Aeris$" "$old_entry"; then
        rm -f "$old_entry" "$ICON_DIR/aeris.png"; removed=1
    fi
    if [[ -d "$old_prefix" && -f "$old_prefix/VERSION" && -x "$old_prefix/.venv/bin/aeris" ]]; then
        rm -rf "$old_prefix"; removed=1
    fi
    if (( removed )); then
        step "Retired the old Aeris install"
        say "    Your library moves across to Aerwave the first time you open it."
        command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
    fi
}

# ---------------------------------------------------------------- uninstall

do_uninstall() {
    local current
    current="$(installed_version)"
    if [[ -z "$current" && ! -d "$PREFIX" ]]; then
        if (( PURGE )); then
            say "Aerwave is not installed at $PREFIX, but --purge was given."
        else
            say "Aerwave is not installed at $PREFIX — nothing to remove."
            return 0
        fi
    else
        step "Removing Aerwave ${current:-} from $PREFIX"
    fi
    rm -rf "$PREFIX"
    rm -f "$LAUNCHER" "$APPS_DIR/aerwave.desktop" "$ICON_DIR/aerwave.png"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

    if (( PURGE )); then
        step "Deleting your library and thumbnails"
        rm -rf "$DATA_DIR" "$CACHE_DIR"
        say "    Removed $DATA_DIR and $CACHE_DIR"
    else
        say ""
        say "Your library and watch history were kept in $DATA_DIR."
        say "${DIM}Add --purge to delete them too.${RESET}"
    fi
    say ""
    say "${GREEN}Aerwave has been removed.${RESET}"
    say "${DIM}Any videos it indexed were only ever read, never modified.${RESET}"
}

do_extract() {
    # Used by tools/make_installer.py to check that what it just packed comes
    # back out whole. Handy by hand too, when you want to read the source a
    # given installer would install.
    mkdir -p "$EXTRACT_TO" || die "cannot write to $EXTRACT_TO"
    extract_payload "$EXTRACT_TO" >/dev/null
    say "Unpacked Aerwave $VERSION into $EXTRACT_TO"
}

case "$ACTION" in
    status)    show_status ;;
    uninstall) do_uninstall ;;
    extract)   do_extract ;;
    install)   do_install ;;
esac
exit 0

__AERWAVE_PAYLOAD_BELOW__
