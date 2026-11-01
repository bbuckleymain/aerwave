"""Running ffmpeg and ffprobe so that quitting does not have to wait for them.

Everything these tools are asked for here is disposable — a thumbnail, a cover,
a duration, the lyrics inside a tag. Each call can take tens of seconds on a
slow file, and a window that is closing should not sit waiting for an answer it
will never show. So the processes are tracked, and closing kills them.

`subprocess.run` cannot do that: it owns the child and only returns when the
child is done. This is the same thing with the handle kept.
"""

import subprocess
import threading

_running: set = set()
_lock = threading.Lock()
_stopped = False


def run(command: list, timeout: float = 60, text: bool = True):
    """Run a tool and wait for it, unless the app is on its way out.

    Returns a CompletedProcess, or None when there was nothing to wait for
    because we are shutting down or the tool is not installed.
    """
    if _stopped:
        return None
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=text)
    except (OSError, ValueError):
        return None
    with _lock:
        _running.add(process)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
    except (OSError, ValueError):
        return None
    finally:
        with _lock:
            _running.discard(process)
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def stop_all() -> None:
    """Cut short everything still running, and start nothing new."""
    global _stopped
    _stopped = True
    with _lock:
        live = list(_running)
    for process in live:
        try:
            process.kill()
        except OSError:
            pass


def stopping() -> bool:
    return _stopped
