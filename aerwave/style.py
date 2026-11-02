"""Colour themes and the application stylesheet.

Three themes ship with the app: Catppuccin Mocha (flat dark), Frutiger Aero
(glossy light glass) and Frutiger Aero Night (the same glass after dark). The
two Aero themes are tuned to the diinki.glass and diinki.aerolight KDE looks —
same backgrounds, same text, and the desktop's own accent and selection blues.
set_theme() swaps the module-level tokens; stylesheet() builds the QSS from
them, so a theme change is just a new setStyleSheet().
"""

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

THEME_NAMES = ("Catppuccin Mocha", "Frutiger Aero", "Frutiger Aero Night")
DEFAULT_THEME = "Frutiger Aero Night"
ART_RADIUS = 4.0               # how round the corners of a cover or thumbnail are

# Every token the stylesheet needs. Gradients are full QSS paint values, so
# flat themes just use "solid" colours and the aero themes get their gloss.
THEMES = {
    "Catppuccin Mocha": {
        "is_dark": True,
        "BG": "#1e1e2e",
        "WINDOW_BG": "#1e1e2e",
        "SURFACE": "#181825",
        "ELEVATED": "#313244",
        "HOVER": "#45475a",
        "BORDER": "#45475a",
        "TEXT": "#cdd6f4",
        "MUTED": "#a6adc8",
        "FAINT": "#7f849c",
        "ACCENT": "#89b4fa",
        "ACCENT_HOVER": "#a3c2fb",
        "ACCENT_DIM": "#74c7ec",
        "ON_ACCENT": "#11111b",
        "WARN": "#f9e2af",
        "GOOD": "#a6e3a1",
        "SCROLL": "#585b70",
        "SCROLL_HOVER": "#6c7086",
        "PROGRESS_CHUNK": "#585b70",
        "SURFACE_GRAD": "#181825",
        "BUTTON_GRAD": "#313244",
        "BUTTON_HOVER": "#45475a",
        "BUTTON_BORDER": "#585b70",
        "PRIMARY_GRAD": "#89b4fa",
        "PRIMARY_HOVER": "#a3c2fb",
        "PRIMARY_BORDER": "#89b4fa",
        "PLAYER_GLASS": "rgba(17, 17, 27, 0.96)",
        "PLAYER_GLASS_TOP": "rgba(17, 17, 27, 0.93)",
        "PLAYER_BORDER": "rgba(255, 255, 255, 0.07)",
        "PLAYER_TEXT": "#cdd6f4",
        "PLAYER_MUTED": "#9399b2",
        "TOAST_BG": "rgba(17, 17, 27, 0.90)",
        "TOAST_BORDER": "rgba(255, 255, 255, 0.09)",
        "RESUME_BG": "rgba(24, 24, 37, 0.94)",
        "SPEED_BG": "rgba(137, 180, 250, 0.92)",
        "SPEED_TEXT": "#11111b",
    },
    "Frutiger Aero": {
        "is_dark": False,
        # the diinki.aerolight scheme: neutral glass, black text, Win7 blue
        "BG": "#f0f0f0",
        # glass over the wallpaper, Konsole-style: the window shows through
        "WINDOW_BG": "#f0f0f0",
        "SURFACE": "#ffffff",
        "ELEVATED": "#fffff9",
        "HOVER": "#cce8ff",
        "BORDER": "#b5c0cc",
        "TEXT": "#000000",
        "MUTED": "#787878",
        "FAINT": "#9e9e9e",
        "ACCENT": "#3399ff",
        "ACCENT_HOVER": "#5fa7e3",
        "ACCENT_DIM": "#cce8ff",
        "ON_ACCENT": "#ffffff",
        "WARN": "#d97b1c",
        "GOOD": "#1f9c68",
        "SCROLL": "#c8c8c8",
        "SCROLL_HOVER": "#adadad",
        "PROGRESS_CHUNK": "#7db9f5",
        # glossy white glass, translucent so the wallpaper glints through
        "SURFACE_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 rgba(255, 255, 255, 0.82),"
                        " stop:0.45 rgba(242, 246, 252, 0.80),"
                        " stop:1 rgba(224, 234, 244, 0.78))",
        "BUTTON_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                       " stop:0 #ffffff, stop:1 #dedede)",
        "BUTTON_HOVER": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 #eaf4fd, stop:1 #cce8ff)",
        "BUTTON_BORDER": "#a8a8a8",
        "PRIMARY_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 #7db9f5, stop:1 #3399ff)",
        "PRIMARY_HOVER": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                         " stop:0 #8ec4f7, stop:1 #4aa3ff)",
        "PRIMARY_BORDER": "#1d6fc0",
        # video chrome stays dark glass in every theme, it sits on the video
        "PLAYER_GLASS": "rgba(7, 32, 48, 0.94)",
        "PLAYER_GLASS_TOP": "rgba(7, 32, 48, 0.90)",
        "PLAYER_BORDER": "rgba(255, 255, 255, 0.14)",
        "PLAYER_TEXT": "#eaf7ff",
        "PLAYER_MUTED": "#9ccbe4",
        "TOAST_BG": "rgba(8, 34, 50, 0.90)",
        "TOAST_BORDER": "rgba(255, 255, 255, 0.16)",
        "RESUME_BG": "rgba(10, 38, 55, 0.94)",
        "SPEED_BG": "rgba(32, 208, 252, 0.92)",
        "SPEED_TEXT": "#03293f",
    },
    "Frutiger Aero Night": {
        "is_dark": True,
        # window, surface and text colours are lifted straight from the
        # diinki.glass colour scheme, so app and desktop read as one
        "BG": "#0a1723",
        # Konsole's Glass colour, 70 % opacity — a hint of wallpaper behind
        "WINDOW_BG": "#0a1723",
        "SURFACE": "#102636",
        "ELEVATED": "#122c3d",
        "HOVER": "#173a52",
        "BORDER": "#3a6b85",
        "TEXT": "#d6ecf6",
        "MUTED": "#8fb2c9",
        "FAINT": "#6190aa",
        "ACCENT": "#5d88cd",
        "ACCENT_HOVER": "#7ba3da",
        "ACCENT_DIM": "#4a76b5",
        "ON_ACCENT": "#0a1723",
        "WARN": "#f0d68f",
        "GOOD": "#7fe3b0",
        "SCROLL": "#2b4a68",
        "SCROLL_HOVER": "#3a6290",
        # the desktop's own selection blue: text stays legible on the chunk
        "PROGRESS_CHUNK": "#44669a",
        # frosted night-ocean glass, translucent like the rest of the window
        "SURFACE_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 rgba(20, 41, 58, 0.82),"
                        " stop:0.5 rgba(14, 34, 49, 0.80),"
                        " stop:1 rgba(10, 26, 40, 0.78))",
        "BUTTON_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                       " stop:0 #1a3d55, stop:1 #122c3d)",
        "BUTTON_HOVER": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 #224c68, stop:1 #173a52)",
        "BUTTON_BORDER": "#3a6b85",
        "PRIMARY_GRAD": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                        " stop:0 #a9c6ee, stop:1 #5d88cd)",
        "PRIMARY_HOVER": "qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                         " stop:0 #b7cef0, stop:1 #6b95d8)",
        "PRIMARY_BORDER": "#4a76b5",
        "PLAYER_GLASS": "rgba(8, 23, 35, 0.96)",
        "PLAYER_GLASS_TOP": "rgba(8, 23, 35, 0.93)",
        "PLAYER_BORDER": "rgba(130, 168, 215, 0.12)",
        "PLAYER_TEXT": "#d6ecf6",
        "PLAYER_MUTED": "#8fb2c9",
        "TOAST_BG": "rgba(9, 25, 38, 0.90)",
        "TOAST_BORDER": "rgba(130, 168, 215, 0.16)",
        "RESUME_BG": "rgba(16, 38, 54, 0.94)",
        "SPEED_BG": "rgba(93, 136, 205, 0.92)",
        "SPEED_TEXT": "#0a1723",
    },
}

_TEMPLATE = """
* { font-family: "Google Sans", "Adwaita Sans", "Inter", "Noto Sans", "Segoe UI", system-ui,
    sans-serif; }

QWidget {
    /* containers stay transparent: only #MainWindowRoot paints the glass,
       otherwise nested layers compound into an opaque window */
    background: transparent;
    color: @TEXT@;
    font-size: 13px;
}

#MainWindowRoot { background: @WINDOW_BG@; }

QLabel {
    background: transparent;   /* otherwise every label paints a patch of BG */
}

QToolTip {
    background: @ELEVATED@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    padding: 4px 7px;
    border-radius: 5px;
}

#Sidebar {
    background: @SURFACE_GRAD@;
    border-left: 1px solid @BORDER@;
}

/* The one column down the left of the window, whichever library is open. */
#Rail {
    background: @SURFACE_GRAD@;
    border-right: 1px solid @BORDER@;
}
#RailRule { background: @BORDER@; }

#TopBar { background: transparent; }
#StatusBar { background: transparent; }

#AppTitle {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 2.4px;
    color: @ACCENT@;
}

#PageHeading {
    font-size: 25px;
    font-weight: 600;
    color: @TEXT@;
}

#SectionLabel {
    color: @MUTED@;
    font-size: 12px;
    font-weight: 500;
    padding: 0 10px;
}

#StatusLabel, #HintLabel { color: @MUTED@; }

#EmptyTitle { font-size: 25px; font-weight: 600; }
#EmptyBody  { color: @MUTED@; font-size: 14px; }

QPushButton {
    background: @BUTTON_GRAD@;
    border: 1px solid @BUTTON_BORDER@;
    border-radius: 6px;
    padding: 7px 13px;
    color: @TEXT@;
}
QPushButton:hover  { background: @BUTTON_HOVER@; }
/* The words sink a pixel: stylesheets cannot scale, but this reads as a press. */
QPushButton:pressed { background: @BORDER@; padding: 8px 13px 6px 13px; }
QPushButton:disabled { color: @FAINT@; background: @SURFACE@; }
/* Keyboard users need to see where they are. Hover is a mouse affordance and
   does not answer that. */
QPushButton:focus { border-color: @ACCENT@; }

QPushButton#Primary {
    background: @PRIMARY_GRAD@;
    border: 1px solid @PRIMARY_BORDER@;
    color: @ON_ACCENT@;
    font-weight: 600;
}
QPushButton#Primary:hover { background: @PRIMARY_HOVER@; }
QPushButton#Primary:pressed { background: @PRIMARY_GRAD@; padding: 8px 13px 6px 13px; }
/* A disabled button has to look disabled whatever else it is: the id selector
   above is more specific than QPushButton:disabled, so without this the one
   button that matters most still looks pressable when it is not. */
QPushButton#Primary:disabled {
    background: @SURFACE@;
    border: 1px solid @BORDER@;
    color: @FAINT@;
    font-weight: 600;
}
QPushButton#Flat:disabled, QToolButton:disabled { background: transparent; }

QPushButton#Flat, QToolButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px;
}
QPushButton#Flat:pressed { padding: 6px; }
QPushButton#Flat:hover, QToolButton:hover { background: @HOVER@; }
QPushButton#Flat:checked, QToolButton:checked { background: @HOVER@; }

QPushButton#NavItem {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 7px 10px;
    text-align: left;
    color: @MUTED@;
}
QPushButton#NavItem:pressed { padding: 7px 10px; }
QPushButton#NavItem:hover { color: @TEXT@; }
QPushButton#NavItem:checked { background: @HOVER@; color: @TEXT@; }

QLineEdit {
    background: @ELEVATED@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 6px 10px 6px 4px;
    selection-background-color: @ACCENT_DIM@;
}
QLineEdit:focus { border-color: @ACCENT@; }

QComboBox {
    background: @BUTTON_GRAD@;
    border: 1px solid @BUTTON_BORDER@;
    border-radius: 6px;
    padding: 6px 10px;
    min-width: 120px;
}
QComboBox:hover { background: @BUTTON_HOVER@; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow {
    image: url("@CHEVRON@");
    width: 12px;
    height: 12px;
    margin-right: 7px;
}
QComboBox QAbstractItemView {
    background: @ELEVATED@;
    border: 1px solid @BORDER@;
    selection-background-color: @ACCENT_DIM@;
    outline: none;
    padding: 4px;
}

QMenu {
    background: @ELEVATED@;
    border: 1px solid @BUTTON_BORDER@;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item { padding: 7px 22px 7px 24px; border-radius: 6px; }
QMenu::item:selected { background: @HOVER@; }
QMenu::separator { height: 1px; background: @BORDER@; margin: 5px 8px; }

QListView {
    background: transparent;
    border: none;
    outline: none;
}

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: @SCROLL@; border-radius: 3px; min-height: 40px;
}
QScrollBar::handle:vertical:hover { background: @SCROLL_HOVER@; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: @SCROLL@; border-radius: 5px; min-width: 40px; }

QProgressBar {
    background: @ELEVATED@;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: @TEXT@;
}
QProgressBar::chunk { background: @PROGRESS_CHUNK@; border-radius: 3px; }

/* Checkboxes, and the checkable rows in a list, share one look. Without the
   tick image a checked box is only a coloured square, which says nothing on
   its own about which way round it is. */
QCheckBox { spacing: 8px; }
QCheckBox::indicator, QListWidget::indicator, QTreeWidget::indicator {
    width: 17px; height: 17px;
    border: 1px solid @BUTTON_BORDER@;
    border-radius: 5px;
    background: @ELEVATED@;
}
QCheckBox::indicator:hover, QListWidget::indicator:hover {
    border-color: @ACCENT@;
}
QCheckBox::indicator:checked, QListWidget::indicator:checked,
QTreeWidget::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
    image: url("@TICK@");
}
QCheckBox::indicator:disabled, QListWidget::indicator:disabled {
    border-color: @BORDER@;
    background: @SURFACE@;
}
QCheckBox:disabled { color: @FAINT@; }

QDialog { background: @SURFACE@; }

#AppBar {
    background: @SURFACE_GRAD@;
    border-bottom: 1px solid @BORDER@;
}

QPushButton#Tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 9px 14px 8px 14px;
    color: @MUTED@;
    font-weight: 600;
}
QPushButton#Tab:hover { color: @TEXT@; }
QPushButton#Tab:checked {
    color: @TEXT@;
    border-bottom: 2px solid @ACCENT@;
}

#PhoneNotice {
    background: @ELEVATED@;
    border: 1px solid @BORDER@;
    border-left: 3px solid @WARN@;
    border-radius: 8px;
    padding: 12px 14px;
    color: @MUTED@;
}

#NowPlaying {
    background: @SURFACE_GRAD@;
    border-top: 1px solid @BORDER@;
}

QTreeWidget#TrackTable {
    background: transparent;
    border: none;
    outline: none;
    alternate-background-color: rgba(255, 255, 255, 0.03);
}
QTreeWidget#TrackTable::item {
    padding: 8px 4px;
    border: none;
    color: @TEXT@;
}
QTreeWidget#TrackTable::item:selected { background: @HOVER@; }
QTreeWidget#TrackTable::item:hover { background: @SURFACE@; }
QHeaderView::section {
    background: transparent;
    color: @MUTED@;
    border: none;
    border-bottom: 1px solid @BORDER@;
    padding: 6px 7px;
    font-size: 12px;
    font-weight: 400;
}

QListWidget#LyricsList {
    background: transparent;
    border: none;
    outline: none;
    font-size: 14px;
}
QListWidget#LyricsList::item {
    padding: 6px 4px;
    border-radius: 6px;
    line-height: 150%;
}
QListWidget#LyricsList::item:hover { background: @HOVER@; }

QProgressBar#SyncProgress { height: 8px; }

#PlayerRoot, #VideoSurface { background: #000000; }
/* The player pages are siblings of the shell in the stack, not children
   of it, so a transparent background here is a hole straight through the
   window rather than a view of the glass behind. */
#BookPlayerRoot { background: @WINDOW_BG@; }

#CoverArt {
    background: @ELEVATED@;
    border: none;
    border-radius: 6px;
}

#NowPlaying { font-size: 14px; line-height: 140%; }

/* The reading pane. No font-size here: the reader sets it, and a stylesheet
   would win over the size the reader remembers. */
#DefinitionPopup {
    background: @ELEVATED@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
}
#DefinitionWord { font-size: 16px; font-weight: 700; color: @TEXT@; }
#DefinitionSense { color: @TEXT@; }
#DefinitionExample { color: @MUTED@; font-style: italic; padding-left: 12px; }

QListWidget#ReaderText {
    background: @SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
    padding: 10px 12px;
    outline: none;
}
QListWidget#ReaderText::item {
    padding: 5px 10px;
    border-radius: 6px;
    line-height: 152%;
}
QListWidget#ReaderText::item:hover { background: @HOVER@; }

QListWidget#ChapterList {
    background: transparent;
    border: none;
    padding: 0;
    outline: none;
}
QListWidget#ChapterList::item {
    padding: 7px 10px;
    border-radius: 6px;
    color: @MUTED@;
}
QListWidget#ChapterList::item:hover { background: @HOVER@; }
QListWidget#ChapterList::item:selected {
    background: @HOVER@;
    color: @TEXT@;
}

/* The wall of candidate covers. Each tile is a picture with its name under it,
   so the selection has to read as a frame around the picture rather than a
   highlight behind the words. */
QListWidget#CoverGrid {
    background: @SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
    padding: 8px;
    outline: none;
}
QListWidget#CoverGrid::item {
    padding: 6px;
    border-radius: 9px;
    border: 1px solid transparent;
    color: @MUTED@;
}
QListWidget#CoverGrid::item:hover {
    background: @HOVER@;
    color: @TEXT@;
}
QListWidget#CoverGrid::item:selected {
    background: @HOVER@;
    border: 1px solid @ACCENT@;
    color: @TEXT@;
}

/* The queue, standing on the stage rather than against a sidebar: it has a
   photograph behind it, so it has to be glass rather than a panel. */
#StageQueue {
    background: rgba(8, 12, 20, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 14px;
}
#StageQueue QLabel { color: @PLAYER_TEXT@; }
#StageQueue #SectionLabel { color: @PLAYER_MUTED@; }

QSplitter#MusicPanes::handle, QSplitter#StagePanes::handle {
    background: transparent;
}
QSplitter#MusicPanes::handle:hover, QSplitter#StagePanes::handle:hover {
    background: @ACCENT@;
}

#ControlBar {
    background: @PLAYER_GLASS@;
    border-top: 1px solid @PLAYER_BORDER@;
}
#PlayerTopBar { background: @PLAYER_GLASS_TOP@; }
#PlayerTitle { font-size: 14px; font-weight: 600; color: @PLAYER_TEXT@; }
#PlayerSubtitle { color: @PLAYER_MUTED@; font-size: 12px; }

/* the music and audiobook pages reuse the video chrome names but sit on
   theme glass, so they take theme colours instead of the light video text */
#PageTitle { font-size: 18px; font-weight: 500; color: @TEXT@; }
#PageSubtitle { color: @MUTED@; font-size: 12px; }
#NowPlaying #PlayerTitle { color: @TEXT@; }
#NowPlaying #PlayerSubtitle, #NowPlaying #TimeLabel { color: @MUTED@; }
#BookPlayerRoot #TimeLabel { color: @MUTED@; }
#TimeLabel { color: @PLAYER_MUTED@; font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }
#Toast {
    background: @TOAST_BG@;
    border: 1px solid @TOAST_BORDER@;
    border-radius: 8px;
    padding: 9px 15px;
    font-size: 14px;
    font-weight: 500;
    color: @PLAYER_TEXT@;
}
#SpeedBadge {
    background: @SPEED_BG@;
    color: @SPEED_TEXT@;
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 15px;
    font-weight: 800;
}
#ResumeBanner {
    background: @RESUME_BG@;
    border: 1px solid @PLAYER_BORDER@;
    border-radius: 10px;
    padding: 8px 12px;
    color: @PLAYER_TEXT@;
}

/* the full-screen music stage: photo behind, light words on top */
QListWidget#StageLyrics {
    background: transparent;
    border: none;
    outline: none;
}
#StageCover, #StageSide { background: transparent; }

/* The size is set in code, scaled to the width the lines have. */
QListWidget#StageLyrics::item { padding: 3px 14px; border-radius: 10px; }

/* The words in the side panel: bigger and bolder than the chrome around
   them, because they are the thing being read. */
QListWidget#LyricsList { font-size: 16px; font-weight: 600; }
#StageTitle { font-size: 24px; font-weight: 600; color: #ffffff; }
#StageSubtitle { color: rgba(255, 255, 255, 0.66); font-size: 13px; }
#StageTime { color: rgba(255, 255, 255, 0.72); font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }
/* the background-tasks dropdown, top right: dressed like a menu */
#TasksPanel {
    background: @ELEVATED@;
    border: 1px solid @BUTTON_BORDER@;
    border-radius: 8px;
}
#TasksPanel QLabel { background: transparent; }
#TaskTitle { color: @TEXT@; font-weight: 600; }
#TaskDoneTitle { color: @TEXT@; }
#TaskMeta { color: @MUTED@; font-size: 12px; }
#TaskQuiet { color: @MUTED@; }
#TaskDone { color: @GOOD@; font-size: 9px; }
#TaskFailed { color: @WARN@; font-size: 9px; }
#TaskRule { background: @BORDER@; margin: 0 10px; }
QProgressBar#TaskBar {
    background: @HOVER@;
    border: none;
    border-radius: 2px;
    min-height: 4px;
    max-height: 4px;
}
QProgressBar#TaskBar::chunk { background: @ACCENT@; border-radius: 2px; }
QPushButton#TaskClear {
    background: transparent;
    border: none;
    color: @ACCENT@;
    font-size: 12px;
    padding: 2px 6px;
}
QPushButton#TaskClear:hover { text-decoration: underline; }

/* compact mode: the player on its own, floating above the work */
#MiniPlayer {
    background: @WINDOW_BG@;
    border: 1px solid @BORDER@;
}
#MiniCover { background: transparent; }
#MiniTitle { font-size: 14px; font-weight: 700; color: @TEXT@; }
#MiniSubtitle { color: @MUTED@; font-size: 12px; }
#MiniTime { color: @MUTED@; font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 11px; }
#MiniPlayer QPushButton#Flat { background: transparent; border: none; border-radius: 8px; }
#MiniPlayer QPushButton#Flat:hover { background: @HOVER@; }

#MusicStage QPushButton#Flat:hover { background: rgba(255, 255, 255, 0.14); }
#MusicStage QPushButton#Flat:checked { background: rgba(255, 255, 255, 0.10); }
"""

_current = THEMES[DEFAULT_THEME]
_current_name = DEFAULT_THEME
_translucent = True
_glass = "frosted"


def load_fonts() -> None:
    """The clock's face travels with the app rather than relying on the desktop."""
    QFontDatabase.addApplicationFont(str(Path(__file__).parent / "fonts" / "jetbrainsmono.ttf"))


def figures_font(base: QFont) -> QFont:
    """For a column of times: figures of one width, so they line up digit for digit."""
    font = QFont(base)
    font.setFamily("JetBrains Mono")
    font.setPixelSize(12)
    return font


def theme_names() -> tuple[str, ...]:
    """Theme names in cycle order."""
    return THEME_NAMES


# How much of the wallpaper the glass lets through. Anything below "solid"
# needs a compositor that blurs behind the window to look like glass rather
# than like a hole — KWin's own Blur effect, or Better Blur, doing the work.
GLASS_LEVELS = {"solid": 1.0, "frosted": 0.88, "clear": 0.70}
GLASS_NAMES = ("solid", "frosted", "clear")


def set_glass(level: str) -> None:
    """How see-through the window is: solid, frosted or clear."""
    global _glass, _translucent
    _glass = level if level in GLASS_LEVELS else "frosted"
    _translucent = _glass != "solid"


def glass() -> str:
    return _glass


def set_translucent(enabled: bool) -> None:
    """The older on/off form of the same thing."""
    set_glass("frosted" if enabled else "solid")


def is_translucent() -> bool:
    return _translucent


def window_bg() -> str:
    """The root background: glass at the chosen strength, or the flat colour."""
    alpha = GLASS_LEVELS.get(_glass, 0.88)
    if alpha >= 1.0:
        return _current["BG"]
    colour = QColor(_current["WINDOW_BG"])
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {alpha})"


def with_alpha(hex_color: str, alpha: float):
    """A QColor of `hex_color` at `alpha` opacity, for card fills on glass."""
    colour = QColor(hex_color)
    colour.setAlphaF(alpha)
    return colour


def current_theme() -> str:
    return _current_name


def set_theme(name: str) -> bool:
    """Switch the module-level colour tokens to a named theme."""
    global _current, _current_name
    theme = THEMES.get(name)
    if theme is None:
        return False
    _current = theme
    _current_name = name
    for key, value in theme.items():
        if key.isupper():
            globals()[key] = value
    return True


def is_dark() -> bool:
    return bool(_current["is_dark"])


def stylesheet(chevron_path: str = "", tick_path: str = "") -> str:
    """The app stylesheet, with its two drawn glyphs pointed at real files.

    Qt will not paint its own arrow or tick over an indicator a stylesheet has
    restyled, so both are rendered to PNGs and named here.
    """
    qss = _TEMPLATE
    for key, value in _current.items():
        if key != "WINDOW_BG":          # depends on the translucency toggle
            qss = qss.replace(f"@{key}@", str(value))
    qss = qss.replace("@WINDOW_BG@", window_bg())
    if tick_path:
        qss = qss.replace("@TICK@", tick_path)
    else:
        qss = qss.replace('image: url("@TICK@");', "")
    if chevron_path:
        return qss.replace("@CHEVRON@", chevron_path)
    # No icon available: fall back to Qt's own arrow rather than an empty box.
    return qss.replace(
        '''QComboBox::down-arrow {
    image: url("@CHEVRON@");
    width: 12px;
    height: 12px;
    margin-right: 7px;
}''', "")


# bind the default theme's tokens eagerly so imports before set_theme()
# (default args, paint helpers) still see real colours
set_theme(DEFAULT_THEME)
