# Aerwave

A native desktop video, audiobook and music player for Linux. Qt 6 for the interface,
libmpv for playback — no browser engine, no Electron, no web app.

Point it at a folder, and it indexes everything inside, pulls artwork out of
each file, and remembers exactly where you stopped.

Three tabs, three libraries: **Videos**, **Audiobooks** and **Music**. They keep
separate folders, separate shelves and separate players, and share one database.

## Videos

- **A library from a folder.** Add one or more folders; Aerwave walks them
  recursively, reads durations with `ffprobe` and extracts thumbnails with
  `ffmpeg` in the background. Rescans on launch pick up new and deleted files.
- **Progress that sticks.** Position is saved every few seconds and whenever you
  leave a video, so reopening it resumes where you were. Finishing a video marks
  it watched, and *Continue watching* collects everything you're partway through.
- **Rebuild library.** The gear menu's *Rebuild library…* wipes the index and
  re-reads every file, for when tags changed underneath Aerwave. Playlists
  survive; watch positions and play counts don't.
- **Full volume control**, 0–200 % with a detent at 100 %, remembered between
  sessions.
- **Skip 5 seconds** with the arrow keys or the two skip buttons — plus 10 s
  (J/L), 60 s (Shift+arrows), frame stepping, and percentage jumps.
- **Hold left-click to run at 2×**, release to drop back to normal speed. The
  hold rate is configurable (1.5×–4×) from the speed menu.
- **Series are grouped.** `S01E04`, `1x04`, `Season 1/Episode 4` and a bare
  episode number inside a `Season 2` folder are all recognised. Shows appear in
  the sidebar, episodes sort in order, each one is titled by its own name rather
  than its filename, and right-clicking a show plays its next unwatched episode.
- **Chapters**, where the file has them: ticks on the scrub bar, chapter names
  in the hover preview, and <kbd>Page Up</kbd>/<kbd>Page Down</kbd> to move
  between them.
- **Your audio and subtitle track choices are remembered per video**, so picking
  the English dub once carries to the next time you open it.
- **Subtitles look after themselves.** An `.srt` beside the video — or in a
  `Subs/` folder, or named `Movie.en.srt` — is found and switched on, and an
  embedded track in your preferred language is selected automatically. You can
  also load a file by hand from the tracks menu.
- **Subtitles can be generated from the audio** for videos that have none. See
  below.
- **The screen stays awake while a video plays.** Watching is invisible to the
  desktop's idle timer, so it would otherwise dim, blank or lock mid-film. Aerwave
  holds a screensaver and power-management inhibition for exactly as long as
  something is actually rolling, and drops it the moment you pause, go back or
  quit.
- Speed presets 0.25×–4×, audio and subtitle track switching, subtitle timing
  nudge, fullscreen, screenshots, autoplay-next, scrub-bar hover previews,
  auto-hiding controls, and drag-and-drop of files or folders onto the window.

### Generated subtitles

For anything with no subtitles at all, Aerwave can transcribe the speech itself
(*Generate subtitles from the audio…* in the tracks menu, or right-click a video
in the library). The result is written as `<name>.whisper.srt` beside the video,
so it loads by itself every time after that, and it appears immediately in
whatever is playing.

Turn on *Generate subtitles when a video has none* in the gear menu and it
happens without being asked.

The engine (`faster-whisper`) is **not** installed with Aerwave — it and its models
weigh far more than the app does. The first time you ask for subtitles, Aerwave
offers to fetch it into its own virtualenv; nothing is added to your system, and
removing Aerwave removes it too. Model size is configurable in the gear menu:

| Model | Download | Speed on a 6-core CPU |
| --- | --- | --- |
| Tiny | 75 MB | ~3.5× realtime |
| Base (default) | 145 MB | ~1.5× realtime |
| Small | 480 MB | slower, noticeably better |
| Medium / Large | 1.5–3.1 GB | slowest, best |

Aerwave tries the GPU first and falls back to the CPU if it turns out to be
unusable — a CUDA version the engine was not built against, or missing cuDNN —
and remembers not to try again. Transcription runs in the background, one video
at a time; you can carry on watching.

## Audiobooks

Add a folder of audiobooks in the **Audiobooks** tab and Aerwave works out what a
book is:

* an **`.m4b`** (or `.m4a`) is one book, and its embedded **chapters** are read
  straight out of the file;
* a **folder of MP3s/FLACs** is one book, with each file as a chapter, sorted
  naturally so *Chapter 2* comes before *Chapter 10*;
* a folder holding exactly one container is that book, named after the folder.

Title, author, narrator and series come from the file's tags, falling back to
folder and file names.

* **Cover art, found four ways.** A picture named after the book beside it, or
  `cover.jpg`/`folder.jpg` in a folder that holds only that book, or the
  artwork embedded in the file. Failing all three, Aerwave asks the catalogue —
  only the book's name goes out, only for books with no art of their own, and
  a book it cannot place is written off rather than asked about again on every
  launch. Turn it off under *Look up missing covers online*. A book with none
  of the four gets a typeset cover made from its title and author.
* **Find cover art…** on any book searches the audiobook and ebook catalogues
  and shows what they have, so you can pick. Change the words if the book is
  listed under another name, or point at a picture of your own.

* **Tidy up the shelf.** Audiobook tags are unreliable — *(Unabridged)* in every
  title, no series, and files that name the first chapter instead of the book.
  **Tidy up the books…** in the gear menu reads each book's name off the shelf,
  looks it up for the author and the series, and shows what it would change
  before changing anything, so *Prelude to The Stormlight Archive* becomes
  **The Way of Kings** with its series filled in. Untick whatever looks wrong;
  a catalogue can be sure of itself and still be wrong about an obscure book.

  Two boxes decide how far it goes, and both start off.

  * **Rename the files** puts them in `Author / Series / 04 - Title.m4b`. Books
    in a series share the series folder rather than each getting one of their
    own, and a book kept as a folder of chapters takes the same place a single
    file would. Whatever sits beside a book — its EPUB, its cover, its chapters
    file — travels with it, and folders left empty are taken away.
  * **Write the names into the tags** changes the files, so other players agree
    with Aerwave about what they are.

  **Everything is written down, and *Undo the last tidy* puts it back** — every
  file where it was, every tag as it read. Nothing is ever written over, and
  nothing is deleted that has not first been copied and checked.

  Renaming a book is an awkward thing to do, because it destroys the evidence
  the new name was read from. An earlier version of this did exactly that, and
  on its second pass had only its own answer left to read — by which point
  "Mistborn #1 - The Final Empire" had become "The Final Empire", and then
  plain "Mistborn", which is what the catalogue calls the first book of that
  series. The fix is not care, it is keeping the evidence: the moment a book is
  about to be renamed or retagged, the name and tag it arrived with are written
  into the database and never written again, and every later pass reads those
  instead of the disk. Tidying the same shelf twice now reaches the same
  answer, and a book whose author nobody knows is left exactly where it is,
  because the shelf is arranged by author and there would be nowhere to put it.

  A book you have tidied also keeps the names you gave it: rewriting its tags
  makes the scanner read the file again, and without this it would hand back
  whatever the tags happen to say. Undo releases it.
* **It keeps your place.** One running position for the whole book, across every
  file in it, saved every few seconds and whenever you stop.
* **Smart rewind.** Coming back after a while nudges the position back a few
  seconds — 5 s after ten minutes away, 30 s after a day — so you re-enter with
  some context instead of mid-sentence.
* **Speed, remembered per book.** 0.5×–3×; the book you race through at 2×
  stays at 2× while another stays at 1×.
* **Sleep timer.** 5–90 minutes or *at the end of this chapter*, with the volume
  easing down over the last few seconds instead of cutting out.
* **Bookmarks.** Press <kbd>B</kbd> to mark a spot; name them, jump between
  them, delete them.
* **Chapter list** with durations, click to jump, current chapter in bold, and
  chapter marks notched into the scrub bar.
* **Read along with the EPUB — the audiobook's synced lyrics.** The **Read** tab
  (<kbd>R</kbd>) shows the book's own text as lines, lights up the line being
  read and keeps it centred, exactly as the lyrics panel does for a song. Click
  a line and the narration goes there.
  Aerwave finds the EPUB itself: beside the book first, then through the folders
  around your audiobook library, matching on title and refusing any volume whose
  number disagrees — text and audio usually live apart, and that is fine. Pick
  the file by hand from the ⚙ menu when the search comes up empty.
* **Find my place, and stay there.** An EPUB carries no timings, so Aerwave takes
  them from the narration: it transcribes a stretch with the same engine used
  for subtitles and matches it to the text. Because the audio is read from the
  file rather than off the speaker, that stretch runs past the playhead — so
  what comes back is a real time for every sentence in the next minute, not a
  single anchor. The panel interpolates between them, then carries on at the
  pace they measured until the next listen takes over. Measured against the
  narration on a Mistborn book, the lit line sits within a second of the voice,
  and it never falls behind it.
  It also rescues books whose embedded chapter marks are wrong, which plenty of
  `.m4b` files are. The ⚙ menu turns the listening off if you would rather it
  did not; the lines then follow an estimate, which drifts.
  **Playback speed needs no special handling**: mpv counts position in the
  file's own seconds, so at 2× those seconds arrive twice as fast, and since
  listening is scheduled by how much narration is still timed, it keeps pace.
* **Line the text up here** is the manual version, for when whole chapters are
  out of step — an omnibus edition, a translation numbered its own way. It pairs
  what is on screen with what is playing, and Aerwave remembers it.
* **Tune it to your eye.** The highlight runs a fraction of a second ahead of
  the voice, because a line lit as the narrator arrives at it reads as in time
  while one lit afterwards reads as late. How much of a lead feels right is
  personal, so <kbd>Z</kbd>/<kbd>X</kbd> nudge the text earlier or later — the
  same nudge the video player gives subtitles — and the setting sticks.
* **Look a word up.** Right-click any word in the text for its meaning, in a
  panel over the page. Definitions come from Wiktionary, which keeps
  Commonwealth spellings as entries of their own — and follows them through, so
  *colour* and *realised* give you the actual sense rather than "Commonwealth
  spelling of color" or "past participle of realise". Inflections resolve the
  same way (*stumbled* → *stumble*), the part of speech the pointer named is
  listed first, and answers are kept on disk so a word is only ever fetched
  once. The word you ask about is the only thing that leaves the machine, and
  only when you ask.
* **Every chapter start is checked, not assumed.** When the narration reaches a
  new chapter, Aerwave shows where the offset says you are and then listens to
  confirm it — because a chapter boundary is exactly where an offset turns out
  to be wrong, and the marks inside an `.m4b` are not evidence of anything.
  Given a deliberately wrong offset, three chapter starts in a row corrected
  themselves to the chapter the narration actually was in.
* **It keeps itself honest.**
* **Search the book** with <kbd>/</kbd>: any line, any chapter, <kbd>Enter</kbd>
  for the next match. **<kbd>+</kbd>/<kbd>−</kbd>** (or A+ / A−) set the reading
  size, and it is remembered.
* **Time remaining, honestly.** "6h 12m left · 4h 08m at 1.5×".
* **Volume levelling** for narration recorded too quietly (gear menu).
* **Keep the computer awake while listening** (gear menu, off by default) — the
  screen is welcome to blank while you listen, but turn this on if your machine
  suspends and cuts the narration off.
* Skip 30 s with the big buttons or arrow keys, 10 s with <kbd>J</kbd>/<kbd>L</kbd>,
  5 minutes with <kbd>Shift</kbd>+arrows.

## Both

Aerwave appears on the session bus as an **MPRIS2 player**, so the keyboard's media
keys drive it from any window, Plasma's media applet shows what is playing with
its artwork and a working scrub bar, and the lock screen can pause it. `Next`
moves to the next video, or the next chapter of an audiobook.

Press **F1** or **?** in the app for the full shortcut list.

### Themes

Three looks, switched live with the palette button in the top bar or
<kbd>Ctrl+T</kbd> — the choice is remembered for next launch.

* **Frutiger Aero** — glossy day theme: sky gradients, glass highlights, cyan
  water tones, in the spirit of [diinki-aero](https://github.com/diinki/diinki-aero).
* **Frutiger Aero Night** — the same glass language after dark: night-ocean
  background, frosted panels, glossy cyan that glows against the dark.
* **Catppuccin Mocha** — the calm, flat dark theme.

**Background** in the gear menu sets how much of the desktop shows through:
*Solid*, *Frosted glass* (the default, 12 % through) or *Clear glass* (30 %).
Glass only looks like glass if your compositor blurs behind the window — KWin's
own **Blur** effect, or **Better Blur**, which can force it on windows that
never ask. Without one, a translucent window just shows the desktop through the
app. On KDE, add `aerwave` to the effect's window list.

## Music

Add a folder of music and Aerwave indexes it by tags — **FLAC, Opus, MP3, M4A,
Ogg, WAV, WavPack, AIFF** and more. Albums and artists are worked out by
grouping the tags, so they can never drift out of step with the files.

Four ways in: **Albums**, **Artists**, **Songs** and **Playlists**.

* **Shuffle albums** queues every album in a random order, each one played in
  its own track order — whole records, one after another, rather than a scramble
  of loose tracks. Select a few albums first to shuffle only those.
* **Like** songs from the list or from the playing bar; liked songs are a
  one-click filter and a ready-made sync selection.
* **Lyrics fetch themselves.** After a scan, tracks without synced lyrics are
  looked up on [LRCLIB](https://lrclib.net): a synced answer replaces any plain
  `.lrc` beside the track and is embedded into the file's lyrics tag as well;
  if LRCLIB only has plain text, that is used instead. Right-clicking songs
  offers *Fetch lyrics* to retry any of them by hand, and the overflow menu
  offers *Re-download every lyric…* to ask again for the whole library.
* **Words timed one at a time.** When *Look for word-by-word lyrics* is on,
  NetEase is asked before LRCLIB, because it is the only free source that
  times every word; the line then lights up word by word rather than all at
  once. Instrumental breaks of five seconds or more show three dots that grow
  as the singing comes back, and <kbd>Z</kbd>/<kbd>X</kbd> nudge the timing by
  a tenth of a second when a file's own timings run late.
* **Full-screen lyrics stage.** Press <kbd>F</kbd> or the expand button in the
  playing bar: the song's words run large over a photo of the artist (pulled
  from Deezer on first play, then cached), the current line lights up, and
  clicking any line seeks there. <kbd>Esc</kbd> leaves.
* **Scrobbling, built in.** Music* → *Scrobbling and Rich Presence…* connects
  Aerwave straight to Last.fm (token flow in the browser — no password stored)
  and to Discord Rich Presence (paste a client ID from a Discord application
  you create). Scrobble rules match Pano's: half the track or four minutes,
  whichever first; offline submissions are queued and flushed later.
  **Rich Presence covers all three libraries**, following whichever player the
  desktop is following: a song with its artist and cover art, an audiobook with
  its author, current chapter and cover, a video as *Watching* — each with a
  progress bar for what is left. Pause, and the line says **Paused** in front
  of whoever it is by, while the bar gives way to a clock counting up from the
  moment you stopped — Discord runs that one itself, so a pause of any length
  costs not one further word to it.

  Nothing is sent that Discord already has. The window offers an update on
  every position tick and almost all of them say what the last one said;
  those are dropped, which keeps the allowance Discord gives for presence
  updates free for the ones that mean something. Three minutes of steady
  playback is two messages, and a pause is one. It is spoken straight to Discord's local
  socket, with no dependency and no account beyond the client ID — and that
  socket is hunted for in every place a Discord client puts it: the plain
  `$XDG_RUNTIME_DIR` of a native install, and the `.flatpak/…/xdg-run`,
  `app/…` and `snap.…` directories the sandboxed builds use. Flatpak changed
  which of its two it uses in 1.15, so looking in only one of them means
  presence stops working the day Discord updates.
  Discord fetches presence art from its own servers, so a cover on disk is no
  use to it: songs use the URL Last.fm holds, and audiobooks one found in
  Apple's public audiobook search. That lookup also settles what the book is
  *called* — tags carry edition notes nobody wants in a status line, and some
  name the first chapter instead of the book, so "Prelude to The Stormlight
  Archive (Unabridged)" shows up as **The Way of Kings**. Names and covers are
  remembered, a wrong-series match is refused in favour of showing none, and
  only the book's name ever leaves the machine — while presence is switched on.
* **Title and artist, your proportions.** The two share the room between them
  in every track list by a proportion rather than a fixed width, so they keep
  their balance at any window size. Drag the line between them in the header,
  or pick from *Title and artist width* in the gear menu or the header's own
  right-click. It is remembered, and every list agrees.
* **Playlists** you build by hand, with drag-free add/remove from any list.
* A queue you can see and reorder, shuffle and repeat, and the usual transport.
  Media keys work here too, through MPRIS.
* **Lyrics that follow the song.** Press <kbd>Y</kbd> for the lyrics panel. Aerwave
  reads an `.lrc` beside the track first, then any lyrics saved inside the file.
  Timed lines highlight as they come round and scroll themselves; click a line to
  jump to it. Untimed lyrics are shown as plain text.

### Converting audio

Right-click any selection of songs and choose *Convert to another format…* for
Opus, MP3, AAC, FLAC or Ogg Vorbis at a quality you pick. Tags come across.
Conversion runs through ffmpeg in the background.

### Syncing to a drive, a disk or a phone

The **Sync** tab keeps a list of destinations. A destination is any folder Aerwave
can write to: external drives appear under `/run/media`, virtual disks wherever
you mount them, and phones under `/run/user/…/gvfs/mtp:host=…`. Aerwave lists what
it can see in the *Detected* box, and *Look again* re-checks.

Destinations can **sync automatically when Aerwave starts** — tick the box and a
quiet run happens a few seconds after launch while the device is mounted.
Unattended runs only ever copy and convert; files are never deleted without
someone pressing Sync in person.

### Phones need a bridge

A phone plugged in over USB speaks **MTP**, which is not a filesystem. Something
has to turn it into a folder before any ordinary program can write to it, and
there are two different mechanisms:

* **gvfs**, with its MTP backend and FUSE daemon, mounts the phone at a real path
  that every application can use (how those are packaged differs by distribution);
* **KDE's KIO** (`kio-extras`) gives Dolphin an `mtp://` address instead, which
  works inside KDE's own dialogs and nowhere else.

That is why a phone can be perfectly visible in Dolphin and invisible to
everything else on the system. If Aerwave sees a phone with no folder behind it, it
says so and prints the exact command for your distribution — on Arch:

```bash
sudo pacman -S gvfs gvfs-mtp
```

Reconnect the phone, open it once in your file manager to mount it, then press
*Look again*.

**[Step-by-step: syncing music to a phone](docs/syncing-a-phone.md)**

For each destination you choose:

| Setting | What it does |
| --- | --- |
| Convert to | Send the originals, or transcode to Opus/MP3/AAC/FLAC on the way |
| What to send | Liked songs, everything, a playlist, or one artist |
| Folder layout | Mirror your library's own folders (default), `Artist/Album/01 Track`, or everything in one folder |
| Album art | Embed it in each file, copy `cover.jpg` beside the tracks, both, or neither |
| Lyrics | Copy `.lrc` files, embed them in each file, both, or neither |
| Delete files not on the list | Removes anything on the device that is no longer selected |

By default the device ends up with **the same folder structure as your
library** — whatever shape it is, genre folders, `CD1`/`CD2`, loose files at the
top, all of it kept. Only the extension changes, and only when the destination
transcodes. If two library folders would collide (both containing `Rock/…`, say)
each keeps its own folder name at the top level so nothing overwrites anything.
A file that somehow sits outside its library folder falls back to
`Artist/Album/Track`.

**Artwork and lyrics travel with the music.** Album art is embedded into each
file where the format allows it — MP3, M4A and FLAC do; Opus and Ogg cannot hold
embedded pictures at all, so for those Aerwave copies `cover.jpg` beside the tracks,
which is what phone players read anyway. Lyrics embed into every format, and can
also travel as `.lrc` files named to match each track.

**Converted files are written to the device and never kept here.** The
conversion goes through a scratch file that is deleted as soon as it has been
handed over, so your computer never ends up holding a second, lossy copy of the
library.

*What would happen?* shows the plan — how many tracks would be sent, how many
are already there, what would be deleted — before anything is touched. Deletion
always asks first, and only ever removes audio files inside the destination.

## Requirements

- Python 3.10+
- `libmpv` (Arch/CachyOS: `mpv`, Debian/Ubuntu: `libmpv2`)
- `ffmpeg` and `ffprobe` for thumbnails and durations

PySide6 and python-mpv are installed into a local virtualenv by the installer,
so nothing lands in your system Python.

## Install

Everything ships as one file. Grab `dist/install-aerwave.sh` and run it:

```bash
./dist/install-aerwave.sh
```

It unpacks the app into `~/.local/lib/aerwave`, builds a virtualenv there, puts a
`aerwave` launcher in `~/.local/bin`, and adds the menu entry. No root required,
and the installer needs no network beyond the first download of the Python
dependencies.

**Updating is the same command** with a newer installer — it reuses the existing
virtualenv, so an update takes a couple of seconds, and your library and watch
history are never touched. If an install ever gets damaged, running it again
notices and repairs it.

```bash
./dist/install-aerwave.sh --status      # bundled vs installed version
./dist/install-aerwave.sh --force       # reinstall / repair
./dist/install-aerwave.sh --uninstall   # remove the app, keep the library
./dist/install-aerwave.sh --help        # prefix, bin-dir, python, and the rest
```

You can also open a single file directly: `aerwave /path/to/video.mkv`.

### Working on the source

`install.sh` is the developer counterpart: it installs *this tree* into `.venv`
in editable mode, so your edits take effect immediately. `./run.sh` launches it
without installing anything.

### Cutting a new version

Bump `__version__` in `aerwave/__init__.py`, then:

```bash
python3 tools/make_installer.py
```

That rebuilds `dist/install-aerwave.sh` and a version-stamped copy beside it. The
installer records what it installed, so a newer file reports "Updating 1.1.0 →
1.2.0" and an older one rolls back cleanly.

## Where your data lives

| What | Path |
| --- | --- |
| Library, watch progress, bookmarks | `~/.local/share/aerwave/library.db` |
| Video thumbnails | `~/.cache/aerwave/thumbnails/` |
| Album and book covers | `~/.cache/aerwave/covers/` |
| Screenshots | `~/Pictures/Aerwave/` |

Deleting `library.db` resets the library; your media files are never touched by
that.

Aerwave reads your media and does not write to it, with one exception you have to
ask for twice: **Tidy up the books…** can rename and move audiobook files and
write their tags, and only when you tick those boxes and confirm the summary it
shows you. Every such change is recorded in the database and *Undo the last
tidy* reverses the whole run. Nothing else in Aerwave ever renames, moves or
deletes anything of yours — syncing writes only inside the destination you
chose, and refuses to prune one that turns out to be a library folder.

The picture caches are swept half a minute after the app starts: cached art
that no file in the library points at any more is deleted, since it can only
ever be regenerated from the files themselves. Nothing is swept while a drive
is unplugged.

## How it is put together

| File | Role |
| --- | --- |
| `aerwave/mpvwidget.py` | `QOpenGLWidget` that libmpv renders into via its render API — native on Wayland, and lets Qt widgets sit on top of the video |
| `aerwave/player.py` | Video player: overlay chrome, transport, shortcuts, progress saving |
| `aerwave/library_view.py` | Video library: sidebar, search, sort, poster grid and its delegate |
| `aerwave/audio_engine.py` | Headless libmpv instance for audiobooks — no video, no surface |
| `aerwave/book_player.py` | Audiobook player: chapters, bookmarks, sleep timer, per-book speed |
| `aerwave/book_library_view.py` | The audiobook shelf |
| `aerwave/book_scanner.py` | Works out what a book is, and reads its tags and chapters |
| `aerwave/book_reader.py` | The Read panel: the EPUB beside the narration |
| `aerwave/dictionary.py` | Word lookups while you read, with the pointers followed |
| `aerwave/epub.py` | Reads an EPUB, finds the one that goes with a book, lines the chapters up |
| `aerwave/media.py` | Runs ffmpeg/ffprobe so quitting can cut them short |
| `aerwave/covers.py` | Cover art: from disk, from the file, from the catalogue, or generated |
| `aerwave/cover_view.py` | Searching for a cover, and choosing one |
| `aerwave/book_lookup.py` | What a book really is: name, author, year, cover |
| `aerwave/book_tidy.py` | Working out and applying corrections to a book's names |
| `aerwave/book_files.py` | Moving a book to where a tidy shelf keeps it, reversibly |
| `aerwave/book_tidy_view.py` | The tidy-up dialog, and the optional pass over new books |
| `aerwave/idle.py` | Holds off screen blanking and sleep while media plays |
| `aerwave/mpris.py` | MPRIS2 on D-Bus: media keys, Plasma's applet, lock screen |
| `aerwave/subtitles.py` | Speech-to-text subtitles, and the one-time engine setup |
| `aerwave/music_player.py` | The music queue, shuffle and repeat rules |
| `aerwave/music_view.py` | Albums, artists, songs, playlists and the playing bar |
| `aerwave/music_scanner.py` | Music import and tag reading |
| `aerwave/converter.py` | ffmpeg format presets, conversion, and its dialog |
| `aerwave/lyrics.py` | Finding lyrics, reading LRC timings, following along |
| `aerwave/lyrics_fetch.py` | The pass that fills lyrics in, on a worker thread |
| `aerwave/lrclib.py` | LRCLIB: lyrics timed line by line |
| `aerwave/netease.py` | NetEase: lyrics timed word by word, converted to enhanced LRC |
| `aerwave/music_stage.py` | The full-screen stage: cover, words and the queue |
| `aerwave/sync.py` | Working out and running a sync to a device |
| `aerwave/sync_view.py` | Managing sync destinations |
| `aerwave/db.py` | SQLite schema and every query, guarded for background threads |
| `aerwave/scanner.py` | Folder walk and `ffprobe` metadata pass on a worker thread |
| `aerwave/thumbs.py` | Thumbnail extraction pool and pixmap cache |
| `aerwave/icons.py` | Every icon, drawn with `QPainter` — the app ships no image assets |
| `tools/installer_template.sh` | The installer/updater logic; the payload is appended to it |
| `tools/make_installer.py` | Packs the tree into `dist/install-aerwave.sh` |
| `tools/installer.sh` | The shell half of that: the installer minus its payload |

## Troubleshooting

**"Could not start the video renderer"** — your GL driver could not give mpv a
context. Try `MPV_HWDEC=no aerwave`, or turn off *Hardware decoding* in the gear
menu and restart.

**A video has no thumbnail** — Aerwave gives up on a file after one failed
`ffmpeg` attempt; right-click the card and choose *Regenerate thumbnail*.

**Files show up missing after moving them** — rescan, then use *Forget files
that are gone* in the library's gear menu.

**A folder of MP3s came in as several books, or several books merged into one**
— the rule is one book per `.m4b`, and one book per folder of loose chapter
files. Put each book in its own folder and rescan.

**An audiobook has no cover** — drop a `cover.jpg` next to it and choose *Look
for cover art again* from its right-click menu.

**A phone does not show up under Detected** — see *Phones need a bridge* above.
Aerwave will name the phone and tell you what is missing. Also check the handset
itself: the USB mode has to be "File transfer (MTP)", not "Charging only".

**A show was not detected** — Aerwave needs an episode marker it can trust:
`S01E04`, `1x04`, `Season 1 Episode 4`, or a plain number inside a folder named
`Season 2`. Files with none of those are treated as one-off videos, which is
what you want for films.

**Subtitles were generated but say nonsense** — try a larger model in the gear
menu; `tiny` and `base` trade accuracy for speed. Deleting the
`<name>.whisper.srt` and generating again starts over.

**Coloured dashes torn across the picture** — that is hardware decoding
misrendering through mpv's render API, which happens on some driver and GPU
combinations and shows up most once subtitles add an overlay pass. Aerwave decodes
in software by default for this reason. If you turned *Hardware decoding* on in
the gear menu and see it, turn it back off and restart.

**The screen still dims during a video** — Aerwave asks
`org.freedesktop.ScreenSaver` and `org.freedesktop.PowerManagement.Inhibit`,
which covers KDE, GNOME, Xfce and Cinnamon, and falls back to `systemd-inhibit`
when neither exists. On an unusual session you can check the fallback is holding
with `systemd-inhibit --list`.

**The installer says the payload is corrupt** — the file was truncated in
transit. Download it again; it verifies its own SHA-256 before unpacking.
