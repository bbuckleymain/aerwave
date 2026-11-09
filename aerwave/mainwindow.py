"""Main window: the Videos and Audiobooks tabs, and the players behind them."""

from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import APP_NAME, lrclib, media, motion, style
from .book_library_view import BookLibraryPage
from .book_player import BookPlayerPage
from .book_scanner import AUDIO_EXTENSIONS, BookScanner
from .book_tidy_view import QuietTidy, TidyDialog
from .covers import CoverCache
from .db import Library
from .discord_rpc import DiscordPresence
from .icons import app_icon, icon, write_chevron, write_tick
from .converter import ConvertDialog
from .lastfm import LastfmScrobbler
from .library_view import LibraryPage
from .lyrics_fetch import LyricsFetcher
from .mpris import MprisService
from .music_player import MusicPlayer
from .music_scanner import MusicScanner
from .mini_player import MiniPlayer
from .tasks import TaskCenter, TasksButton
from .music_stage import MusicStage
from .music_view import MusicPage
from .sync_view import SyncView
from .player import PlayerPage
from .scanner import ScanController
from .subtitles import SetupDialog, Transcriber, engine_available
from .thumbs import ThumbCache
from .widgets import ElidedLabel, chosen_glass, typing_now

VIDEO_TAB, BOOK_TAB, MUSIC_TAB = 0, 1, 2
RAIL_WIDTH = 232
HEADER_HEIGHT = 64
PAGE_GUTTER = 24

SHORTCUTS = [
    ("Video playback", [
        ("Space  /  K", "Play or pause"),
        ("Click the video", "Play or pause"),
        ("Hold left mouse button", "Fast-forward at 2× until you let go"),
        ("← / →", "Skip back / forward 5 seconds"),
        ("Shift + ← / →", "Skip back / forward 60 seconds"),
        ("J / L", "Skip back / forward 10 seconds"),
        (", / .", "Step one frame back / forward"),
        ("Page Up / Page Down", "Previous / next chapter, when the file has them"),
        ("F  /  double-click", "Fullscreen"),
        ("V / A", "Cycle subtitle / audio tracks"),
        ("Subtitles", "Files beside the video, or in a Subs/ folder, load on their own"),
        ("Generate subtitles", "Tracks menu, or right-click a video in the library"),
        ("Z / X", "Nudge subtitle timing"),
        ("N / P", "Next / previous video"),
        ("W", "Mark as watched"),
        ("S", "Save the current frame as an image"),
    ]),
    ("Audiobooks", [
        ("Space  /  K", "Play or pause"),
        ("← / →", "Back / forward 30 seconds"),
        ("J / L", "Back / forward 10 seconds"),
        ("Shift + ← / →", "Back / forward 5 minutes"),
        ("N / P", "Next chapter / restart this chapter"),
        ("B", "Bookmark this spot"),
        ("T", "Sleep timer"),
        ("F", "Mark the book as finished"),
        ("R", "Read the book's text alongside the narration"),
        (", / .", "Turn the page back / forward in the text"),
        ("/", "Search the book's text"),
        ("Right-click a word", "Look it up in the dictionary"),
        ("+ / −", "Bigger / smaller reading text"),
        ("Z / X", "Nudge the text earlier / later against the voice"),
        ("Find my place", "Listens to a minute and pins the text to it"),
        ("Speed", "Remembered separately for every book"),
    ]),
    ("Both players", [
        ("↑ / ↓", "Volume up / down 5 %"),
        ("M", "Mute"),
        ("[ / ]", "Slower / faster"),
        ("\\", "Reset speed to 1×"),
        ("0 – 9", "Jump to 0 % – 90 %"),
        ("Home / End", "Jump to the start / end"),
        ("Backspace", "Restart from the beginning (video)"),
        ("Esc", "Leave fullscreen, or go back"),
    ]),
    ("Anywhere", [
        ("F11", "Fill the screen, or go back to a window"),
        ("Media keys", "Play/pause, next and previous, from any window"),
        ("Plasma's media applet", "Shows what is playing, with artwork and a scrub bar"),
    ]),
    ("Music", [
        ("Y", "Show or hide the lyrics"),
        ("Q", "Show or hide the queue"),
        ("F", "The lyrics stage; Esc leaves"),
        ("Z / X", "Nudge the lyrics earlier or later"),
        ("Ctrl + M", "Compact mode: a small window above everything else"),
        ("Shuffle albums", "Queues whole albums in a random order"),
    ]),
    ("Library", [
        ("Ctrl + T", "Cycle theme: Mocha, Aero, Aero Night"),
        ("Ctrl + 1 / 2 / 3", "Switch to Videos / Audiobooks / Music"),
        ("Right-click a show", "Play its next unwatched episode"),
        ("Ctrl + F", "Search"),
        ("Ctrl + O", "Add a folder to the current tab"),
        ("F5", "Rescan folders"),
        ("Enter / Space", "Play the selection"),
        ("? or F1", "This list"),
    ]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard and mouse shortcuts")
        self.resize(680, 680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(22, 20, 22, 20)
        column.setSpacing(18)

        for section, rows in SHORTCUTS:
            heading = QLabel(section.upper())
            heading.setObjectName("SectionLabel")
            column.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(7)
            grid.setColumnStretch(1, 1)
            for row, (keys, description) in enumerate(rows):
                key_label = QLabel(keys)
                key_label.setStyleSheet(
                    f"color:{style.TEXT}; font-weight:600; font-family:'JetBrains Mono',monospace;")
                key_label.setMinimumWidth(190)
                grid.addWidget(key_label, row, 0)
                grid.addWidget(QLabel(description), row, 1)
            column.addLayout(grid)
        column.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        footer = QHBoxLayout()
        footer.setContentsMargins(22, 0, 22, 16)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        outer.addLayout(footer)


class OnlineServicesDialog(QDialog):
    """Set up Last.fm scrobbling and Discord Rich Presence.

    Last.fm never sees a password: Aerwave asks for a token, the browser opens,
    you approve, and the token is traded for a session key. Discord only needs
    the client ID of an application you own.
    """

    def __init__(self, scrobbler: "LastfmScrobbler", discord: "DiscordPresence",
                 parent=None):
        super().__init__(parent)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scrobbler = scrobbler
        self.discord = discord
        self.setWindowTitle("Scrobbling and Rich Presence")
        self.setMinimumWidth(560)

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(12)

        heading = QLabel("Online services")
        heading.setObjectName("EmptyTitle")
        column.addWidget(heading)

        saved = scrobbler.library.get("lastfm", {})
        form = QFormLayout()
        form.setSpacing(9)
        self.api_key = QLineEdit(str(saved.get("api_key", "")))
        self.api_key.setPlaceholderText("from last.fm/api/accounts")
        form.addRow("Last.fm API key", self.api_key)
        self.secret = QLineEdit(str(saved.get("secret", "")))
        self.secret.setPlaceholderText("the shared secret next to the key")
        form.addRow("Shared secret", self.secret)
        self.discord_id = QLineEdit(str(saved.get("discord_client_id", "")))
        self.discord_id.setPlaceholderText("from discord.com/developers/applications")
        form.addRow("Discord client ID", self.discord_id)
        self.musixmatch_key = QLineEdit(str(scrobbler.library.get("musixmatch_key", "") or ""))
        self.musixmatch_key.setPlaceholderText("from developer.musixmatch.com")
        self.musixmatch_key.setToolTip(
            "Asked first for lyrics when set. Timed lyrics need a paid Musixmatch\n"
            "plan; a free key gets no timings and is not used for anything else.")
        form.addRow("Musixmatch API key", self.musixmatch_key)
        column.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setObjectName("HintLabel")
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect Last.fm…")
        self.connect_button.clicked.connect(self._connect)
        buttons.addWidget(self.connect_button)
        self.approve_button = QPushButton("I've approved")
        self.approve_button.clicked.connect(self._approved)
        self.approve_button.hide()
        buttons.addWidget(self.approve_button)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self._disconnect)
        buttons.addWidget(self.disconnect_button)
        buttons.addStretch(1)
        close = QPushButton("Save and close")
        close.setObjectName("Primary")
        close.clicked.connect(self._save_and_close)
        buttons.addWidget(close)
        column.addLayout(buttons)

        self._refresh()

    def _refresh(self) -> None:
        connected = self.scrobbler.connected
        self.connect_button.setEnabled(self.scrobbler.client.configured)
        self.disconnect_button.setEnabled(connected)
        bits = [self.scrobbler.credentials_summary()]
        bits.append("Discord: "
                    + ("on" if self.discord.enabled else "no client ID set"))
        library = self.scrobbler.library
        if not str(library.get("musixmatch_key", "") or "").strip():
            bits.append("Musixmatch: no key")
        else:
            # What Musixmatch last said about the key, when it refused one.
            bits.append("Musixmatch: " + (library.get("musixmatch_status", "")
                                          or "key set"))
        self.status_label.setText(" · ".join(bits))

    def _connect(self) -> None:
        self._save()
        if not self.scrobbler.client.configured:
            self.status_label.setText("API key and secret are needed first.")
            return
        try:
            self.scrobbler.client.request_token()
        except Exception as exc:
            self.status_label.setText(f"Last.fm unreachable: {str(exc)[:120]}")
            return
        url = self.scrobbler.start_connect()
        self.status_label.setText("Approve in the browser, then press "
                                  "\"I've approved\".")
        self.approve_button.show()
        _ = url

    def _approved(self) -> None:
        try:
            username = self.scrobbler.finish_connect()
        except Exception as exc:
            self.status_label.setText(f"Not approved yet? ({str(exc)[:120]})")
            return
        self.status_label.setText(f"Connected as {username}")
        self.approve_button.hide()
        self._refresh()

    def _disconnect(self) -> None:
        self.scrobbler.disconnect()
        self._refresh()

    def _save(self) -> None:
        self.scrobbler.save_credentials(self.api_key.text(),
                                        self.secret.text(),
                                        self.discord_id.text())
        self.discord.set_client_id(self.discord_id.text())
        library = self.scrobbler.library
        key = self.musixmatch_key.text().strip()
        if key != str(library.get("musixmatch_key", "") or ""):
            library.set("musixmatch_key", key)
            library.set("musixmatch_status", "")   # a new key, a clean slate

    def _save_and_close(self) -> None:
        self._save()
        self.discord.restart()
        self.accept()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon(128))
        self.resize(1280, 820)

        self.library = Library()
        self.thumbs = ThumbCache(self)
        self.covers = CoverCache(self, self.library)
        self.transcriber = Transcriber(self.library, self)
        self.scanner = ScanController(self.library, self)
        self.book_scanner = ScanController(self.library, self, worker_cls=BookScanner)
        self.music_scanner = ScanController(self.library, self, worker_cls=MusicScanner)
        self.lyrics_scanner = ScanController(self.library, self, worker_cls=LyricsFetcher)
        self._timing_job = None        # the word-timing run, when one is going
        self._from_keyboard = False    # the stage was asked for by a shortcut
        self.tasks = TaskCenter(self)  # everything running in the background
        self.mini = None               # compact mode, built the first time
        self.music_player = MusicPlayer(self.library, self)
        self.music_stage = MusicStage(self.music_player, self.library, self.covers, self)
        self.lastfm = LastfmScrobbler(self.music_player, self.library, self)
        self.discord = DiscordPresence(self.library, self.lastfm, self)
        self._presence_track: int | None = None
        self.quiet_tidy = QuietTidy(self.library, self)
        self.quiet_tidy.done.connect(lambda changed: changed and
                                     self.book_library_page.reload())
        self.discord.restart()
        self._rebuild_pending = False
        self._playlist_backup: list[tuple[str, str]] = []

        self.stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self.library_page = LibraryPage(self.library, self.thumbs, self)
        self.book_library_page = BookLibraryPage(self.library, self.covers, self)
        self.player_page = PlayerPage(self.library, self)
        self.book_player_page = BookPlayerPage(self.library, self.covers, self)
        self.sync_view = SyncView(self.library, self)
        self.music_page = MusicPage(self.library, self.music_player, self.covers,
                                    self.sync_view, self)

        self.stack.addWidget(self._build_shell())
        self.stack.addWidget(self.player_page)
        self.stack.addWidget(self.book_player_page)
        self.stack.addWidget(self.music_stage)

        self._connect()
        self._install_shortcuts()

        # Media keys, Plasma's media applet and lock-screen controls.
        self.mpris = MprisService(self, self)
        self.mpris.register()
        self._connect_mpris()

        self._reload_throttle = QTimer(self)
        self._reload_throttle.setSingleShot(True)
        self._reload_throttle.setInterval(1200)
        self._reload_throttle.timeout.connect(self._reload_visible_library)

        self.setAcceptDrops(True)
        # Glass over the wallpaper, like the desktop's own terminals: the
        # alpha channel has to be on before the platform window exists.
        self._glass = chosen_glass(self.library)
        self._translucent = self._glass != "solid"
        style.set_glass(self._glass)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, self._translucent)
        # Custom QWidget subclasses ignore stylesheet backgrounds unless this
        # is set; the root needs it to paint the glass at all.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._restore_geometry()
        self.set_tab(int(self.library.get("tab", VIDEO_TAB)))
        self.library_page.reload()
        self.book_library_page.reload()
        self.music_page.reload()
        QTimer.singleShot(600, lambda: self.rescan(quiet=True, both=True))
        # devices are usually mounted by now; quiet copies only, never deletions
        QTimer.singleShot(9000, self.sync_view.run_startup_syncs)
        # Long after everything that matters, sweep the picture caches. They
        # are keyed by a file's path and date, so a renamed or replaced file
        # leaves its old picture behind and nothing ever asks for it again.
        QTimer.singleShot(30000, self._sweep_caches)

    # ------------------------------------------------------------------ build

    def _build_shell(self) -> QWidget:
        shell = QWidget()
        # the one surface that paints the theme glass; a child of the window,
        # because translucent top-levels skip stylesheet backgrounds entirely
        shell.setObjectName("MainWindowRoot")
        shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Left, one column whichever library is open: the name, the three
        # libraries, then what the open one has to choose from.
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(RAIL_WIDTH)
        side = QVBoxLayout(rail)
        side.setContentsMargins(12, 0, 12, 14)
        side.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(10, 0, 0, 0)
        brand.setSpacing(9)
        mark = QLabel()
        mark.setPixmap(icon("logo", 20, style.ACCENT).pixmap(20, 20))
        brand.addWidget(mark)
        name = QLabel(APP_NAME.upper())
        name.setObjectName("AppTitle")
        brand.addWidget(name)
        brand.addStretch(1)
        heading_row = QWidget()
        heading_row.setFixedHeight(HEADER_HEIGHT)
        heading_row.setLayout(brand)
        side.addWidget(heading_row)

        self.tab_buttons = []
        for index, (text, icon_name) in enumerate((("Videos", "film"),
                                                   ("Audiobooks", "headphones"),
                                                   ("Music", "note"))):
            button = QPushButton(f"  {text}")
            button.setObjectName("NavItem")
            button.setCheckable(True)
            button.setIcon(icon(icon_name, 16, style.MUTED))
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, i=index: self.set_tab(i))
            side.addWidget(button)
            self.tab_buttons.append(button)
        side.addSpacing(10)
        rule = QWidget()
        rule.setObjectName("RailRule")
        rule.setFixedHeight(1)
        side.addWidget(rule)
        side.addSpacing(10)

        self.rail_stack = QStackedWidget()
        for page in (self.library_page, self.book_library_page, self.music_page):
            self.rail_stack.addWidget(page.sidebar)
        side.addWidget(self.rail_stack, 1)

        # Across the top, the same for all three: what you are looking at on
        # the left, what to do about it on the right.
        header = QWidget()
        header.setObjectName("TopBar")
        header.setFixedHeight(HEADER_HEIGHT)
        row = QHBoxLayout(header)
        row.setContentsMargins(PAGE_GUTTER, 0, PAGE_GUTTER - 8, 0)
        row.setSpacing(6)
        self.page_heading = ElidedLabel("")
        self.page_heading.setObjectName("PageHeading")
        self.page_heading.setMinimumWidth(120)
        row.addWidget(self.page_heading, 1)

        self.tools_stack = QStackedWidget()
        self.tools_stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for page in (self.library_page, self.book_library_page, self.music_page):
            self.tools_stack.addWidget(page.toolbar)
            page.headingChanged.connect(self._heading_changed)
        row.addWidget(self.tools_stack)

        # cycles through the themes, icon shows the sun when it is dark out
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("Flat")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.theme_button.setToolTip(f"Theme: {style.current_theme()}  (Ctrl+T)")
        self.theme_button.setFixedSize(33, 33)
        self.theme_button.setIconSize(QSize(17, 17))
        self.theme_button.clicked.connect(self._cycle_theme)
        motion.press_dip(self.theme_button)
        self._refresh_theme_icon()
        # What is running in the background, and what just finished.
        self.tasks_button = TasksButton(self.tasks, self)
        row.addWidget(self.tasks_button)
        row.addWidget(self.theme_button)

        self.library_stack = QStackedWidget()
        self.library_stack.addWidget(self.library_page)
        self.library_stack.addWidget(self.book_library_page)
        self.library_stack.addWidget(self.music_page)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(header)
        main.addWidget(self.library_stack, 1)

        frame = QHBoxLayout(shell)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        frame.addWidget(rail)
        frame.addLayout(main, 1)
        return shell

    def _heading_changed(self, text: str) -> None:
        if self.sender() is self.current_library():
            self.page_heading.setText(text)

    def _connect(self) -> None:
        self.library_page.playRequested.connect(self.play_video)
        self.library_page.folderAdded.connect(lambda f: self.add_folder(f, "video"))
        self.library_page.rescanRequested.connect(lambda: self.rescan())
        self.library_page.rebuildRequested.connect(self.rebuild_library)
        self.library_page.glassChanged.connect(self.set_glass)
        self.library_page.helpRequested.connect(self.show_shortcuts)

        self.book_library_page.playRequested.connect(self.play_book)
        self.book_library_page.folderAdded.connect(lambda f: self.add_folder(f, "audio"))
        self.book_library_page.rescanRequested.connect(lambda: self.rescan())
        self.book_library_page.rebuildRequested.connect(self.rebuild_library)
        self.book_library_page.glassChanged.connect(self.set_glass)
        self.book_library_page.helpRequested.connect(self.show_shortcuts)
        self.book_library_page.tidyRequested.connect(self.tidy_books)

        self.player_page.backRequested.connect(self.show_library)
        self.player_page.stepRequested.connect(self.play_neighbour)
        self.player_page.progressChanged.connect(self.library_page.refresh_video)
        self.player_page.fullscreenRequested.connect(self.set_fullscreen)
        self.player_page.helpRequested.connect(self.show_shortcuts)
        self.player_page.transcribeRequested.connect(self.generate_subtitles)
        self.player_page.cancelTranscribeRequested.connect(self.transcriber.cancel)
        self.library_page.transcribeRequested.connect(self.generate_subtitles)
        self.library_page.cancelTranscribeRequested.connect(self.transcriber.cancel)
        self.transcriber.started.connect(self._on_transcribe_started)
        self.transcriber.progress.connect(self._on_transcribe_progress)
        self.transcriber.finished.connect(self._on_transcribe_finished)
        self.transcriber.failed.connect(self._on_transcribe_failed)

        self.book_player_page.backRequested.connect(self.show_library)
        self.book_player_page.progressChanged.connect(self.book_library_page.refresh_book)
        self.book_player_page.helpRequested.connect(self.show_shortcuts)
        self.covers.ready.connect(self.book_player_page.cover_updated)

        self.music_page.folderAdded.connect(lambda f: self.add_folder(f, "music"))
        self.music_page.rescanRequested.connect(lambda: self.rescan())
        self.music_page.rebuildRequested.connect(self.rebuild_library)
        self.music_page.glassChanged.connect(self.set_glass)
        self.music_page.stageRequested.connect(self.show_music_stage)
        self.music_page.compactRequested.connect(self.enter_compact)
        # The way off the stage, which Esc also takes.
        self.music_stage.exitRequested.connect(self.show_library)
        self.music_stage.fullScreenRequested.connect(self.toggle_fullscreen)
        self.music_page.wordSyncChanged.connect(self.music_stage.set_word_sync)
        self.music_page.servicesRequested.connect(self.show_services_dialog)
        self.music_page.convertRequested.connect(self.convert_tracks)
        self.music_page.lyricsRequested.connect(self.fetch_lyrics)
        self.music_page.lyricsRedoRequested.connect(self.redownload_lyrics)
        self.music_page.lyricsTimeRequested.connect(self.time_lyrics)
        self.music_page.helpRequested.connect(self.show_shortcuts)
        self.music_player.trackChanged.connect(lambda _t: self.mpris.track_changed())
        self.music_player.trackChanged.connect(self._refresh_presence)
        self.music_player.pausedChanged.connect(self._refresh_presence)
        self.music_player.positionChanged.connect(self._refresh_presence)
        # pausedChanged only fires on transitions, and playback starts running —
        # seed the presence with the player's actual state
        self.discord.update_paused(self.music_player.paused)
        for signal in (self.music_player.pausedChanged, self.music_player.queueChanged,
                       self.music_player.volumeChanged):
            signal.connect(lambda *_: self.mpris.refresh())

        for controller, on_finish in ((self.scanner, self._on_scan_finished),
                                      (self.book_scanner, self._on_scan_finished),
                                      (self.music_scanner, self._on_music_scan_finished)):
            controller.progress.connect(self._on_scan_progress)
            controller.changed.connect(self._on_scan_changed)
            controller.finished.connect(on_finish)

        self.lyrics_scanner.progress.connect(self._on_lyrics_progress)
        self.lyrics_scanner.changed.connect(self._on_scan_changed)
        self.lyrics_scanner.finished.connect(self._on_lyrics_finished)
        self._connect_tasks()

    def _connect_tasks(self) -> None:
        """Have every background worker report to the tasks button too.

        Each keeps reporting to its own page's status line as before; this
        is the one place that sees all of them at once.
        """
        tasks = self.tasks

        def scanned(added: int, removed: int) -> str:
            bits = [f"{added} new"] if added else []
            if removed:
                bits.append(f"{removed} gone")
            return " · ".join(bits) or "Already up to date"

        def lyricked(synced: int, plain: int) -> str:
            bits = [f"{synced} synced"] if synced else []
            if plain:
                bits.append(f"{plain} plain only")
            return " · ".join(bits) or "Nothing new to add"

        for controller, key, title, note in (
                (self.scanner, "scan-video", "Scanning videos", scanned),
                (self.book_scanner, "scan-books", "Scanning audiobooks", scanned),
                (self.music_scanner, "scan-music", "Scanning music", scanned),
                (self.lyrics_scanner, "lyrics", "Fetching lyrics", lyricked)):
            controller.started.connect(
                lambda c=controller, k=key, t=title: tasks.begin(k, t, cancel=c.cancel))
            controller.progress.connect(
                lambda done, total, label, k=key, t=title:
                tasks.update(k, done, total, label, title=t))
            controller.finished.connect(
                lambda a, b, k=key, n=note: tasks.finish(k, n(a, b)))

        # Subtitles: one task per film, each stoppable on its own.
        transcriber = self.transcriber
        transcriber.started.connect(
            lambda video_id, title: tasks.begin(
                f"subs-{video_id}", f"Subtitles · {title}",
                cancel=lambda v=video_id: transcriber.cancel(v), counted=False))
        transcriber.progress.connect(
            lambda video_id, fraction, phase: tasks.update(
                f"subs-{video_id}", int(fraction * 100), 100, phase))
        transcriber.finished.connect(
            lambda video_id, _path: tasks.finish(f"subs-{video_id}", "Ready to watch"))
        transcriber.failed.connect(
            lambda video_id, message: tasks.finish(f"subs-{video_id}", message, failed=True))

        # Syncs start from the Sync page and, on their own, after launch.
        sync = self.sync_view.controller

        def synced(done: int, total: int, what: str) -> None:
            if not tasks.has("sync"):
                tasks.begin("sync", "Syncing to a device", cancel=sync.cancel)
            tasks.update("sync", done, total, what)

        def sync_over(copied: int, covers: int, deleted: int, failed: int, error: str) -> None:
            if error:
                tasks.finish("sync", error, failed=True)
                return
            bits = [f"{copied} sent"] if copied else []
            if deleted:
                bits.append(f"{deleted} removed")
            if failed:
                bits.append(f"{failed} failed")
            tasks.finish("sync", " · ".join(bits) or "Already in step", failed=bool(failed))

        sync.progress.connect(synced)
        sync.finished.connect(sync_over)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.set_tab(VIDEO_TAB))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.set_tab(BOOK_TAB))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self.set_tab(MUSIC_TAB))
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._cycle_theme)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.current_library().focus_search())
        QShortcut(QKeySequence("Ctrl+O"), self, activated=lambda: self.current_library().choose_folder())
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.rescan())
        QShortcut(QKeySequence("F1"), self, activated=self.show_shortcuts)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_compact)
        # F used to be read in keyPressEvent, which only sees a key nothing
        # else wanted — so with the cursor in the search box it typed an f
        # and the stage never opened. A shortcut is heard first; the guard
        # hands the letter back when something is being typed into.
        QShortcut(QKeySequence("F"), self, activated=self._stage_shortcut)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)

    def _connect_mpris(self) -> None:
        refresh = lambda *_: self.mpris.refresh()
        video = self.player_page.video_widget
        for signal in (video.pausedChanged, video.volumeChanged, video.speedChanged,
                       video.mutedChanged, video.fileLoaded, video.durationChanged):
            signal.connect(refresh)
        engine = self.book_player_page.engine
        for signal in (engine.pausedChanged, engine.volumeChanged, engine.speedChanged,
                       engine.mutedChanged, engine.fileLoaded, engine.durationChanged):
            signal.connect(refresh)
        self.player_page.seeked.connect(self.mpris.seeked)
        self.book_player_page.seeked.connect(self.mpris.seeked)

        # Discord follows the same player the desktop does.
        for signal in (video.pausedChanged, video.positionChanged, video.fileLoaded,
                       engine.pausedChanged, engine.positionChanged, engine.fileLoaded):
            signal.connect(self._refresh_presence)
        self.stack.currentChanged.connect(self._refresh_presence)

    # -------------------------------------------------------------- presence

    def _refresh_presence(self, *_args) -> None:
        """Tell Discord what is playing now.

        Books and videos go through `update_media`; songs keep their own path,
        which fetches cover art from Last.fm. Everything here is safe to call on
        every position tick — the presence itself decides what is worth sending.
        """
        if not self.discord.enabled:
            return
        page = self._mpris_page()
        if page is None:
            self.discord.update_media(None)
            self._presence_track = None
            self.discord.update_track({})
            return
        # Whether it is paused comes first. It used to be settled last, which
        # meant that un-pausing pushed the old paused card and the new playing
        # one in the same breath — and Discord, seeing two frames at once,
        # showed the first of them for a good while.
        self.discord.update_paused(self._engine_of(page).paused)
        if page is self.music_player:
            self.discord.update_media(None)
            track = self.music_player.current or {}
            if track.get("id") != self._presence_track:
                self._presence_track = track.get("id")
                self.discord.update_track(track)
        elif page is self.book_player_page:
            book = page.book
            chapter = ""
            if page.chapters:
                mark = page.chapters[page.chapter_at(page.elapsed)]
                chapter = mark.get("title") or ""
            self.discord.update_media({
                "id": book["id"],
                "title": book["title"],
                "by": book.get("author") or book.get("series") or "",
                "series": book.get("series") or "",
                "path": book.get("path") or "",
                "chapter": chapter,
                "duration": page._total_duration(),
            }, kind="book")
        else:
            video = page.video
            self.discord.update_media({
                "id": video["id"],
                "title": video["title"],
                "by": Path(video["path"]).parent.name,
                "duration": page._duration or video.get("duration") or 0,
            }, kind="video")
        self.discord.update_position(self.mpris_position())

    # ------------------------------------------------------------- subtitles

    def generate_subtitles(self, video: dict) -> None:
        """Start a transcription, offering to fetch the engine the first time."""
        if not engine_available():
            dialog = SetupDialog(self)
            dialog.exec()
            if not dialog.installed:
                return
        if self.transcriber.is_queued(video["id"]):
            return
        if self.transcriber.busy:
            self.library_page.status_label.setText(
                f"Queued subtitles for {video['title']}")
        self.transcriber.start(video)

    def _is_on_screen(self, video_id: int) -> bool:
        return (self.stack.currentWidget() is self.player_page
                and self.player_page.video is not None
                and self.player_page.video["id"] == video_id)

    def _on_transcribe_started(self, video_id: int, title: str) -> None:
        self.library_page.set_transcribing(video_id, 0.0)
        if self._is_on_screen(video_id):
            self.player_page.set_transcribing(True)
        self.library_page.status_label.setText(f"Generating subtitles for {title}…")

    def _on_transcribe_progress(self, video_id: int, fraction: float, phase: str) -> None:
        self.library_page.set_transcribing(video_id, fraction)
        if self._is_on_screen(video_id):
            self.player_page.show_transcribe_progress(fraction, phase)
        else:
            self.library_page.status_label.setText(
                f"Subtitles: {phase.lower()} {int(fraction * 100)}%")

    def _on_transcribe_finished(self, video_id: int, path: str) -> None:
        self.library_page.set_transcribing(video_id, None)
        name = Path(path).name
        if self._is_on_screen(video_id):
            self.player_page.set_transcribing(False)
            self.player_page.add_subtitle(path)
        else:
            self.library_page.status_label.setText(f"Subtitles ready: {name}")
        self.library_page.refresh_video(video_id)

    def _on_transcribe_failed(self, video_id: int, message: str) -> None:
        self.library_page.set_transcribing(video_id, None)
        if self._is_on_screen(video_id):
            self.player_page.set_transcribing(False)
            self.player_page.toast.flash(f"Subtitles: {message}", 4000)
        self.library_page.status_label.setText(f"Subtitles failed — {message}")

    # ---------------------------------------------------------------- MPRIS

    def _mpris_page(self):
        """The player the desktop should be controlling, if any.

        A video or audiobook on screen wins; otherwise music, which keeps
        playing while you browse the library.
        """
        current = self.stack.currentWidget()
        if current is self.player_page and self.player_page.video is not None:
            return self.player_page
        if current is self.book_player_page and self.book_player_page.book is not None:
            return self.book_player_page
        if self.music_player.current is not None:
            return self.music_player
        return None

    def _music_is_active(self) -> bool:
        return self._mpris_page() is self.music_player

    def mpris_status(self) -> str:
        page = self._mpris_page()
        if page is None:
            return "Stopped"
        return "Paused" if self._engine_of(page).paused else "Playing"

    def _engine_of(self, page):
        if page is self.player_page:
            return page.video_widget
        return page.engine

    def mpris_metadata(self) -> dict:
        page = self._mpris_page()
        if page is None:
            return {}
        if page is self.music_player:
            track = self.music_player.current or {}
            from .music_player import album_cover_source
            cover = self.covers.path_for(album_cover_source(
                {"album": track.get("album"), "album_artist": track.get("album_artist"),
                 "sample_path": track.get("path"), "tracks": 1}))
            return {
                "title": track.get("title") or "",
                "artist": track.get("artist") or "",
                "album": track.get("album") or "",
                "length": float(track.get("duration") or 0),
                "art_url": QUrl.fromLocalFile(str(cover)).toString() if cover.exists() else "",
                "url": QUrl.fromLocalFile(track.get("path", "")).toString(),
            }
        if page is self.player_page:
            video = page.video
            art = self.thumbs.path_for(video)
            return {
                "title": video["title"],
                "artist": Path(video["path"]).parent.name,
                "length": page._duration or video.get("duration") or 0,
                "art_url": QUrl.fromLocalFile(str(art)).toString() if art.exists() else "",
                "url": QUrl.fromLocalFile(video["path"]).toString(),
            }
        book = page.book
        cover = self.covers.path_for(book)
        return {
            "title": book["title"],
            "artist": book.get("author") or "",
            "album": book.get("series") or book["title"],
            "length": page._total_duration(),
            "art_url": QUrl.fromLocalFile(str(cover)).toString() if cover.exists() else "",
            "url": QUrl.fromLocalFile(book["path"]).toString(),
        }

    def mpris_position(self) -> float:
        page = self._mpris_page()
        if page is None:
            return 0.0
        if page is self.music_player:
            return page.engine.position
        return page._position if page is self.player_page else page.elapsed

    def mpris_volume(self) -> float:
        page = self._mpris_page()
        if page is None:
            return float(self.library.get("volume", 100)) / 100.0
        engine = self._engine_of(page)
        return 0.0 if engine.muted else engine.volume / 100.0

    def mpris_set_volume(self, value: float) -> None:
        page = self._mpris_page()
        if page is None:
            return
        self._engine_of(page).set_volume(max(0.0, min(2.0, value)) * 100.0)

    def mpris_rate(self) -> float:
        page = self._mpris_page()
        if page is None:
            return 1.0
        return self._engine_of(page).speed

    def mpris_set_rate(self, value: float) -> None:
        page = self._mpris_page()
        if page is not None and hasattr(page, "set_speed"):
            page.set_speed(value)

    def mpris_play(self) -> None:
        page = self._mpris_page()
        if page is not None:
            self._engine_of(page).set_paused(False)

    def mpris_pause(self) -> None:
        page = self._mpris_page()
        if page is not None:
            self._engine_of(page).set_paused(True)

    def mpris_play_pause(self) -> None:
        page = self._mpris_page()
        if page is not None:
            page.toggle_pause()
        elif self.music_player.queue:
            self.music_player.toggle_pause()

    def mpris_stop(self) -> None:
        if self._music_is_active():
            self.music_player.stop()
        elif self._mpris_page() is not None:
            self.show_library()

    def mpris_next(self) -> None:
        page = self._mpris_page()
        if page is self.music_player:
            self.music_player.next_track()
            return
        if page is self.player_page:
            self.play_neighbour(1)
        elif page is self.book_player_page:
            page.next_chapter()

    def mpris_previous(self) -> None:
        page = self._mpris_page()
        if page is self.music_player:
            self.music_player.previous_track()
            return
        if page is self.player_page:
            self.play_neighbour(-1)
        elif page is self.book_player_page:
            page.previous_chapter()

    def mpris_seek(self, seconds: float) -> None:
        page = self._mpris_page()
        if page is self.music_player:
            self.music_player.seek(page.engine.position + seconds)
            return
        if page is self.player_page:
            page.skip(seconds)
        elif page is self.book_player_page:
            page.skip(seconds)

    def mpris_set_position(self, seconds: float) -> None:
        page = self._mpris_page()
        if page is self.music_player:
            self.music_player.seek(seconds)
            return
        if page is self.player_page:
            page.video_widget.seek_absolute(seconds)
        elif page is self.book_player_page:
            page.seek_book(seconds)

    def mpris_open_uri(self, uri: str) -> None:
        path = QUrl(uri).toLocalFile()
        if path:
            self.open_external(path)

    def mpris_can_go_next(self) -> bool:
        page = self._mpris_page()
        if page is self.music_player:
            return len(self.music_player.queue) > 1
        if page is self.player_page and page.video is not None:
            return self.library_page.neighbour(page.video["id"], 1) is not None
        if page is self.book_player_page:
            return page.chapter_at(page.elapsed) + 1 < len(page.chapters)
        return False

    def mpris_can_go_previous(self) -> bool:
        page = self._mpris_page()
        if page is self.music_player:
            return len(self.music_player.queue) > 1
        if page is self.player_page and page.video is not None:
            return self.library_page.neighbour(page.video["id"], -1) is not None
        return page is self.book_player_page

    def mpris_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def mpris_quit(self) -> None:
        self.close()

    # ------------------------------------------------------------------- tabs

    @property
    def tab(self) -> int:
        return self.library_stack.currentIndex()

    def current_library(self):
        if self.tab == MUSIC_TAB:
            return self.music_page
        return self.book_library_page if self.tab == BOOK_TAB else self.library_page

    def set_tab(self, index: int) -> None:
        index = index if index in (VIDEO_TAB, BOOK_TAB, MUSIC_TAB) else VIDEO_TAB
        self.library_stack.setCurrentIndex(index)
        self.rail_stack.setCurrentIndex(index)
        self.tools_stack.setCurrentIndex(index)
        self.page_heading.setText(self.current_library().heading)
        names = {VIDEO_TAB: "film", BOOK_TAB: "headphones", MUSIC_TAB: "note"}
        for position, button in enumerate(self.tab_buttons):
            button.setChecked(position == index)
            button.setIcon(icon(names[position], 16,
                                style.TEXT if position == index else style.MUTED))
        self.library.set("tab", index)
        if self.stack.currentIndex() != 0:
            self.show_library()

    def _cycle_theme(self) -> None:
        names = list(style.theme_names())
        index = (names.index(style.current_theme()) + 1) % len(names)
        style.set_theme(names[index])
        self.library.set("theme", names[index])   # remember it for next launch
        self._refresh_theme_icon()
        self.theme_button.setToolTip(f"Theme: {style.current_theme()}  (Ctrl+T)")
        QApplication.instance().setStyleSheet(
            style.stylesheet(write_chevron(style.MUTED), write_tick(style.ON_ACCENT)))
        # tab icons were tinted for the old theme, redo them
        self.set_tab(self.tab)

    def set_glass(self, level: str) -> None:
        """How see-through the window is; Wayland needs a fresh platform window."""
        level = level if level in style.GLASS_LEVELS else "frosted"
        enabled = level != "solid"
        self._glass = level
        self.library.set("glass", level)
        self.library.set("translucent", enabled)
        style.set_glass(level)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, enabled)
        QApplication.instance().setStyleSheet(
            style.stylesheet(write_chevron(style.MUTED), write_tick(style.ON_ACCENT)))
        if bool(self._translucent) != bool(enabled):
            self.hide()
            self.show()
        self._translucent = enabled

    def _refresh_theme_icon(self) -> None:
        # moon while a light theme is up, sun when it is dark out
        self.theme_button.setIcon(icon("moon" if not style.is_dark() else "sun",
                                       17, style.TEXT))

    # ------------------------------------------------------------- navigation

    def play_video(self, video: dict, resume: bool = True) -> None:
        if not Path(video["path"]).exists():
            QMessageBox.warning(self, "File missing",
                                f"{video['path']}\n\nThis file is no longer on disk.")
            return
        self.book_player_page.stop()
        self.stack.setCurrentWidget(self.player_page)
        self.player_page.play(video, resume=resume)
        self.setWindowTitle(f"{video['title']} — {APP_NAME}")
        self.mpris.track_changed()

    def play_book(self, book: dict, resume: bool = True) -> None:
        if not Path(book["path"]).exists():
            QMessageBox.warning(self, "Book missing",
                                f"{book['path']}\n\nThis book is no longer on disk.")
            return
        self.player_page.stop()
        self.set_fullscreen(False)
        self.stack.setCurrentWidget(self.book_player_page)
        self.book_player_page.play(book, resume=resume)
        self.setWindowTitle(f"{book['title']} — {APP_NAME}")
        self.mpris.track_changed()

    def play_neighbour(self, direction: int) -> None:
        current = self.player_page.video
        if not current:
            return
        target = self.library_page.neighbour(current["id"], direction)
        if target is None:
            self.player_page.toast.flash(
                "End of the list" if direction > 0 else "Start of the list")
            return
        self.play_video(self.library.video(target["id"]) or target, resume=True)

    def show_library(self) -> None:
        leaving_stage = self.stack.currentWidget() is self.music_stage
        if self.stack.currentWidget() is self.player_page:
            self.player_page.stop()
        elif self.stack.currentWidget() is self.book_player_page:
            self.book_player_page.stop()
        if leaving_stage and not self._from_keyboard:
            motion.dissolve(self)
        self._from_keyboard = False
        if not leaving_stage:
            # The players go full screen by themselves, so leaving one undoes
            # it. The stage does not, so a full screen there was asked for and
            # is left alone.
            self.set_fullscreen(False)
        self.stack.setCurrentIndex(0)
        self.current_library().reload()
        self.setWindowTitle(APP_NAME)
        self.mpris.refresh(force=True)

    def set_fullscreen(self, enabled: bool) -> None:
        stage = getattr(self, "music_stage", None)
        if stage is not None:
            stage.set_full(enabled)
        if enabled and not self.isFullScreen():
            self._pre_fullscreen_maximised = self.isMaximized()
            self.showFullScreen()
        elif not enabled and self.isFullScreen():
            if getattr(self, "_pre_fullscreen_maximised", False):
                self.showMaximized()
            else:
                self.showNormal()

    def toggle_fullscreen(self) -> None:
        """The whole screen, or back to a window — anywhere in the app."""
        self.set_fullscreen(not self.isFullScreen())

    def _stage_shortcut(self) -> None:
        if typing_now() or self.stack.currentIndex() != 0 or self.tab != MUSIC_TAB:
            return
        self._from_keyboard = True       # a shortcut does not wait for a fade
        self.show_music_stage()

    def show_music_stage(self) -> None:
        """The stage: the record on one side, its words on the other.

        A page of the window, not a takeover of the screen. It used to force
        the window full screen, which meant the stage could only ever be one
        size and watching lyrics next to anything else was impossible. F11
        still makes it full screen for anyone who wants that.
        """
        if not self.music_player.current:
            # Silence used to be the whole answer here, which looked like a
            # button that did nothing.
            self.music_page.status_label.setText("Play something first.")
            return
        if not self._from_keyboard:
            motion.dissolve(self)
        self._from_keyboard = False
        self.stack.setCurrentIndex(3)

    # ---------------------------------------------------------- compact mode

    def toggle_compact(self) -> None:
        """Into the small window, or back out of it."""
        if self.mini is not None and self.mini.isVisible():
            self.leave_compact()
        else:
            self.enter_compact()

    def enter_compact(self) -> None:
        """Put the window away and leave the player floating above the work."""
        if self.mini is None:
            self.mini = MiniPlayer(self.music_player, self.covers)
            self.mini.expandRequested.connect(self.leave_compact)
            self.mini.place()
        # Full screen is not something to come back out to from a 430px
        # window, and neither is the stage.
        if self.stack.currentWidget() is self.music_stage:
            self.show_library()
        self.set_fullscreen(False)
        self._was_maximised = self.isMaximized()
        self.mini.show()
        self.mini.raise_()
        self.mini.activateWindow()
        self.hide()

    def leave_compact(self) -> None:
        """Back to the window, exactly as it was left."""
        if self.mini is not None:
            self.mini.hide()
        if getattr(self, "_was_maximised", False):
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_services_dialog(self) -> None:
        OnlineServicesDialog(self.lastfm, self.discord, self).exec()

    def show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    # ---------------------------------------------------------------- library

    def add_folder(self, folder: str, kind: str = "video") -> None:
        self.library.add_folder(folder, kind)
        self._page_for(kind).reload()
        self.rescan(roots=[folder], kind=kind)

    def _page_for(self, kind: str):
        return {"audio": self.book_library_page, "music": self.music_page}.get(
            kind, self.library_page)

    def convert_tracks(self, tracks: list) -> None:
        if tracks:
            ConvertDialog(tracks, self).exec()

    def rescan(self, roots: list[str] | None = None, quiet: bool = False,
               kind: str | None = None, both: bool = False) -> None:
        if both:
            targets = ("video", "audio", "music")
        elif kind:
            targets = (kind,)
        else:
            targets = ({VIDEO_TAB: "video", BOOK_TAB: "audio",
                        MUSIC_TAB: "music"}[self.tab],)
        controllers = {"video": self.scanner, "audio": self.book_scanner,
                       "music": self.music_scanner}
        started = False
        for target in targets:
            controller = controllers[target]
            page = self._page_for(target)
            folders = roots if roots is not None else self.library.folders(target)
            if not folders:
                continue
            if controller.busy:
                continue
            page.rescan_button.setEnabled(False)
            if not quiet:
                page.status_label.setText("Scanning…")
            controller.start(roots)
            started = True
        if not started and not quiet and roots is None:
            self.current_library().choose_folder()

    def _sweep_caches(self) -> None:
        """Housekeeping: drop cached covers and posters nothing points at."""
        if self.scanner.busy or self.book_scanner.busy:
            QTimer.singleShot(30000, self._sweep_caches)
            return                     # a scan may be about to ask for them
        if self.library.anything_missing():
            return                     # a drive is unplugged; its art still counts
        self.covers.sweep(self.library.books())
        self.thumbs.sweep(self.library.videos())

    def _on_scan_progress(self, done: int, total: int, label: str) -> None:
        self.current_library().status_label.setText(f"Scanning {done}/{total} · {label}")

    def _on_scan_changed(self) -> None:
        if not self._reload_throttle.isActive():
            self._reload_throttle.start()

    def _reload_visible_library(self) -> None:
        if self.stack.currentIndex() == 0:
            self.current_library().reload()

    def tidy_books(self) -> None:
        """Show what the shelf would become, and let it be applied.

        A scan walks the same files a tidy would move, so it is stopped first;
        and the book being listened to is held open by mpv, so it goes in as
        off limits.
        """
        if self.book_scanner.busy:
            self.book_scanner.stop()
        playing = self.book_player_page.book
        dialog = TidyDialog(self.library, self,
                            busy=(playing["id"],) if playing else ())
        dialog.applied.connect(self.book_library_page.reload)
        dialog.exec()
        self.book_library_page.reload()

    def _on_scan_finished(self, added: int, removed: int) -> None:
        for page in (self.library_page, self.book_library_page, self.music_page):
            page.rescan_button.setEnabled(True)
            page.reload()
        if added or removed:
            bits = []
            if added:
                bits.append(f"{added} new")
            if removed:
                bits.append(f"{removed} missing")
            self.current_library().status_label.setText(" · ".join(bits))
            QTimer.singleShot(4000, self.current_library()._refresh_stats)
        self._maybe_restore_playlists()
        self.quiet_tidy.start()

    def _on_music_scan_finished(self, added: int, removed: int) -> None:
        self._on_scan_finished(added, removed)
        self.fetch_lyrics()

    # ---------------------------------------------------------------- rebuild

    def rebuild_library(self) -> None:
        """Wipe the index and rebuild it from the files themselves.

        Everything the scanners can regenerate is dropped — watch positions and
        play counts with it. Playlists are the one thing worth keeping, so they
        go into `_playlist_backup` and come back once the rescan is done.
        """
        if not any(self.library.folders(kind) for kind in ("video", "audio", "music")):
            return
        answer = QMessageBox.question(
            self, "Rebuild library",
            "Erase the whole index and rebuild it from your files?\n\n"
            "Watch positions and play counts are lost, playlists are kept. "
            "Every file is read again, which can take a while.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Ok:
            return
        for controller in (self.scanner, self.book_scanner, self.music_scanner,
                           self.lyrics_scanner):
            controller.stop()
        self._playlist_backup = self.library.reset_library()
        self._rebuild_pending = True
        self.library_page.reload()
        self.book_library_page.reload()
        self.music_page.reload()
        self.rescan(quiet=False, both=True)

    def _maybe_restore_playlists(self) -> None:
        """After a rebuild, once no scanner is busy any more."""
        if not self._rebuild_pending:
            return
        if any(controller.busy for controller in
               (self.scanner, self.book_scanner, self.music_scanner)):
            return
        self._rebuild_pending = False
        self.library.restore_playlists(self._playlist_backup)
        self._playlist_backup = []
        self.music_page.reload()

    # ----------------------------------------------------------------- lyrics

    def fetch_lyrics(self, tracks: list | None = None) -> None:
        """Fill in lyrics from LRCLIB for the library, or just `tracks`."""
        if tracks is None and not self.library.get("lyrics_autofetch", True):
            return
        if self.lyrics_scanner.busy or self.music_scanner.busy:
            return
        self.lyrics_scanner.start(payload=tracks)

    def redownload_lyrics(self) -> None:
        """Ask every provider again for every song in the library.

        The caches that stop Aerwave asking the same questions on every launch
        are cleared for this one pass, and lyrics already in place no longer
        count as an answer — but a worse-timed set never replaces a better one.
        """
        if self.lyrics_scanner.busy or self.music_scanner.busy:
            QMessageBox.information(self, "Busy",
                                    "A scan is running. Try again when it has finished.")
            return
        total = len(self.library.tracks())
        if QMessageBox.question(
                self, "Re-download every lyric",
                f"Ask LRCLIB and NetEase again for all {total} songs?\n\n"
                "This takes a while — about a second a song — and replaces the\n"
                "words beside each file, though never with a worse-timed version.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.library.set("lyrics_redo", True)
        self.lyrics_scanner.start()

    def time_lyrics(self, tracks: list) -> None:
        """Put a time to every word of some songs' lyrics, on this machine.

        A speech model listens to the track and says when each word is sung;
        the words themselves stay as the lyrics file has them. It is slow —
        minutes a song on a CPU — so it asks first and runs one song at a
        time in the background, and the rest of the app carries on.
        """
        from . import lyric_align                   # noqa: PLC0415  (optional)
        if not tracks:
            return
        if self._timing_job is not None:
            QMessageBox.information(self, "Already timing",
                                    "One batch at a time — this is slow work.")
            return
        engine = lyric_align.engine()
        if not engine:
            QMessageBox.information(self, "No speech model", lyric_align.missing_note())
            return
        if QMessageBox.question(
                self, "Time the lyrics",
                f"Listen to {len(tracks)} song(s) with {engine} and put a time to "
                "every word?\n\nIt runs here, not over the internet, and takes a "
                "few minutes a song. The words stay as they are — only their "
                "timings change.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        job = lyric_align.TimingJob(list(tracks), lyric_align.model_for(self.library),
                                    str(self.library.get("whisper_language", "") or ""))
        job.signals.progress.connect(self._on_timing_progress)
        job.signals.finished.connect(self._on_timing_finished)
        self._timing_job = job
        self.tasks.begin("timing", "Timing lyrics word by word", cancel=job.cancel)
        self.music_page.status_label.setText("Timing lyrics…")
        QThreadPool.globalInstance().start(job)

    def _on_timing_progress(self, done: int, total: int, name: str) -> None:
        self.music_page.status_label.setText(f"Timing lyrics {done}/{total} · {name}")
        self.tasks.update("timing", done - 1, total, name)

    def _on_timing_finished(self, timed: int, missed: int) -> None:
        self._timing_job = None
        note = f"Timed {timed} song(s) word by word"
        if missed:
            note += f" · {missed} could not be timed"
        self.music_page.status_label.setText(note)
        self.tasks.finish("timing", note, failed=bool(missed and not timed))
        self.music_page.reload_lyrics()
        self.music_stage.forget_lyrics()
        QTimer.singleShot(8000, self.music_page._refresh_stats)

    def _on_lyrics_progress(self, done: int, total: int, label: str) -> None:
        self.music_page.status_label.setText(f"Lyrics {done}/{total} · {label}")

    def _on_lyrics_finished(self, synced: int, plain: int) -> None:
        self.music_page.reload()
        if not synced and not plain:
            return
        bits = [f"Lyrics: {synced} synced"]
        if plain:
            bits.append(f"{plain} plain only")
        self.music_page.status_label.setText(" · ".join(bits))
        QTimer.singleShot(6000, self.music_page._refresh_stats)

    def open_external(self, path: str) -> None:
        """Play a file handed to us on the command line, indexing it on the way."""
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            QMessageBox.warning(self, "File missing", f"{file_path}\n\nThat file does not exist.")
            return
        suffix = file_path.suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            if suffix == ".m4b":
                # the one true audiobook container — everything else is a song
                self._open_external_book(file_path)
            else:
                self._open_external_music(file_path)
            return
        from .scanner import pretty_title, probe

        stat = file_path.stat()
        video_id, needs_probe = self.library.upsert_video(
            str(file_path), str(file_path.parent), pretty_title(file_path),
            stat.st_size, stat.st_mtime)
        if needs_probe:
            self.library.set_probe(video_id, *probe(str(file_path)))
        video = self.library.video(video_id)
        if video:
            self.set_tab(VIDEO_TAB)
            self.library_page.reload()
            self.play_video(video)

    def _open_external_music(self, file_path: Path) -> None:
        """Index one audio file into the music library and play it."""
        from .music_scanner import read_tags
        stat = file_path.stat()
        track_id, needs_probe = self.library.upsert_track(
            str(file_path), str(file_path.parent), stat.st_size, stat.st_mtime)
        if needs_probe:
            self.library.set_track_tags(track_id, read_tags(str(file_path)))
        track = self.library.track(track_id)
        if track:
            self.set_tab(MUSIC_TAB)
            self.music_page.reload()
            self.music_player.set_queue([track], 0)

    def _open_external_book(self, file_path: Path) -> None:
        from .book_scanner import clean_name, describe

        stat = file_path.stat()
        book_id, needs_probe = self.library.upsert_book(
            str(file_path), str(file_path.parent), clean_name(file_path.stem),
            stat.st_size, stat.st_mtime, 1)
        if needs_probe:
            details = describe(book_id, str(file_path), [str(file_path)],
                               clean_name(file_path.stem))
            self.library.set_book_details(
                book_id, title=details["title"], author=details["author"],
                narrator=details["narrator"], series=details["series"],
                duration=details["duration"], tracks=details["tracks"])
        book = self.library.book(book_id)
        if book:
            self.set_tab(BOOK_TAB)
            self.book_library_page.reload()
            self.play_book(book)

    # ------------------------------------------------------------ drag & drop

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        folders = [p for p in paths if p.is_dir()]
        files = [p for p in paths if p.is_file()]
        kind = "audio" if self.tab == BOOK_TAB else "video"
        for folder in folders:
            self.add_folder(str(folder), kind)
        if files and not folders:
            self.open_external(str(files[0]))
        if folders or files:
            event.acceptProposedAction()

    # -------------------------------------------------------------- lifecycle

    def _restore_geometry(self) -> None:
        saved = self.library.get("window_geometry")
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
                return
            except (ValueError, TypeError):
                pass
        screen = QGuiApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(1360, int(available.width() * 0.82)),
                        min(880, int(available.height() * 0.85)))
            self.move(available.center() - self.rect().center())

    def closeEvent(self, event) -> None:
        # Out of sight first. Shutting everything down takes a moment — threads
        # to leave, mpv to let go — and none of it is worth watching.
        self.hide()
        QApplication.processEvents()
        media.stop_all()          # ffmpeg and ffprobe are told to stop first;
        self._reload_throttle.stop()   # everything below waits on those threads
        self.player_page.shutdown_timers()
        self.book_player_page.shutdown_timers()
        self.player_page.save_progress(flush=True)
        self.book_player_page.save_progress(flush=True)
        if not self.isFullScreen():
            self.library.set("window_geometry",
                             bytes(self.saveGeometry().toBase64()).decode("ascii"))
        self.mpris.unregister()
        self.scanner.stop()
        self.book_scanner.stop()
        self.transcriber.shutdown()
        self.sync_view.shutdown()
        self.music_scanner.stop()
        # The only worker that may be mid-request to a web service, and the
        # one that must not be abandoned half way. See ScanController.stop.
        self.lyrics_scanner.stop(wait_ms=lrclib.TIMEOUT * 1000 + 1000)
        self.discord.shutdown()
        self.music_player.shutdown()
        self.thumbs.shutdown()
        self.covers.shutdown()
        self.quiet_tidy.shutdown()
        self.lastfm.shutdown()
        if self.mini is not None:
            self.mini.hide()           # a stray window would outlive the app
        self.music_stage.shutdown()
        self.music_page.lyrics_panel.shutdown()
        self.player_page.video_widget.shutdown()
        self.book_player_page.engine.shutdown()
        self.library.close()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Question and self.stack.currentIndex() == 0:
            self.show_shortcuts()
            return
        if key == Qt.Key.Key_Escape and self.stack.currentIndex() == 3:
            self.show_library()
            return
        # Q on the stage, where the music page's own Q shortcut cannot reach
        # without the two of them shadowing each other.
        if key == Qt.Key.Key_Q and self.stack.currentWidget() is self.music_stage:
            self.music_stage.toggle_queue()
            return
        super().keyPressEvent(event)
