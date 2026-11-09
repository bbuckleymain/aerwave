"""The checks that guard against losing things: what a sync deletes, and
lyrics never being swapped for worse ones. Runs under pytest, or on its own:
`python tests/test_sync_and_lyrics.py`."""

import os
import tempfile
from pathlib import Path

from aerwave import lyrics, sync


class _Library:
    """Just enough of the library for a sync plan."""

    def __init__(self, tracks):
        self._tracks = tracks
        self.settings = {}

    def tracks(self, **_filters):
        return list(self._tracks)

    def folders(self, _kind):
        return []

    def get(self, key, default=None):
        return self.settings.get(key, default)


def _setup(music_present: bool):
    music = Path(tempfile.mkdtemp())
    device = Path(tempfile.mkdtemp())
    song = music / "Artist" / "Album" / "01 Song.mp3"
    if music_present:
        song.parent.mkdir(parents=True)
        song.write_bytes(b"song")
    old = device / "Old" / "gone.mp3"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    track = {"path": str(song), "root": str(music), "title": "Song", "artist": "Artist",
             "album": "Album", "album_artist": "Artist", "track_no": 1}
    target = {"path": str(device), "selection": "all", "delete_extra": 1, "fmt": "copy",
              "layout": "mirror", "artwork": "none"}
    return _Library([track]), target, old


def test_unmounted_music_deletes_nothing():
    library, target, old = _setup(music_present=False)
    plan = sync.build_plan(library, target)
    assert not plan.removals and plan.error, plan
    assert old.exists()


def test_extras_are_removed_when_the_music_is_there():
    library, target, old = _setup(music_present=True)
    plan = sync.build_plan(library, target)
    assert plan.removals == [old] and not plan.error, plan


def test_a_half_copied_file_is_sent_again():
    library, target, _old = _setup(music_present=True)
    plan = sync.build_plan(library, target)
    destination = plan.transfers[0][1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"so")                     # newer than the song, but cut short
    assert not sync.build_plan(library, target).transfers
    library.settings["sync_unfinished"] = str(destination)
    assert [d for _t, d in sync.build_plan(library, target).transfers] == [destination]


def test_lyrics_rank_word_over_line_over_plain():
    worded = "[00:01.00] <00:01.00> Hello <00:01.50> there <00:02.00>\n"
    lined = "[00:01.00] Hello there\n"
    assert lyrics.rank(worded) == 2 and lyrics.rank(lined) == 1
    assert lyrics.rank("Hello there") == 0 and lyrics.rank("") < 1


if __name__ == "__main__":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for name, check in list(globals().items()):
        if name.startswith("test_"):
            check()
            print("ok", name)
