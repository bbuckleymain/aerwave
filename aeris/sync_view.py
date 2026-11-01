"""The Sync tab: places to copy the music to, and running the copy."""

from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QThread, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from .converter import DEFAULT_QUALITY, FORMATS, QUALITIES
from .devices import DeviceFinder
from .icons import icon
from .sync import build_plan, SyncWorker

TARGET_ROLE = Qt.ItemDataRole.UserRole + 1


class SyncController(QObject):
    """Runs one sync on a worker thread."""

    progress = Signal(int, int, str)
    finished = Signal(int, int, int, int, str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self._thread: QThread | None = None
        self._worker: SyncWorker | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, target: dict) -> bool:
        if self.busy:
            return False
        self._thread = QThread()
        self._worker = SyncWorker(self.library, target)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress)
        self._worker.finished.connect(self._done)
        self._thread.start()
        return True

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _done(self, copied: int, covers: int, deleted: int, failed: int, error: str) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.quit()
            thread.wait(4000)
        self._worker = None
        self.finished.emit(copied, covers, deleted, failed, error)

    def stop(self) -> None:
        self.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)


class SyncView(QWidget):
    """Manage the places music gets copied to, and copy it."""

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.controller = SyncController(library, self)
        self.controller.progress.connect(self._on_progress)
        self.controller.finished.connect(self._on_finished)
        self._editing: dict | None = None
        self._startup_queue: list[dict] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)
        heading = QLabel("DESTINATIONS")
        heading.setObjectName("SectionLabel")
        left.addWidget(heading)
        self.list = QListWidget()
        self.list.setObjectName("ChapterList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.currentItemChanged.connect(self._selected)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        left.addWidget(self.list, 1)
        add = QPushButton("  Add a destination")
        add.setObjectName("Primary")
        add.setIcon(icon("device", 16, "#08111d"))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self.new_target)
        left.addWidget(add)
        holder = QWidget()
        holder.setFixedWidth(250)
        holder.setLayout(left)
        row.addWidget(holder)

        right = QVBoxLayout()
        right.setContentsMargins(20, 16, 20, 16)
        right.setSpacing(12)

        self.title = QLabel("No destination selected")
        self.title.setObjectName("EmptyTitle")
        right.addWidget(self.title)
        blurb = QLabel(
            "A destination is any folder Aeris can write to — an external drive, a "
            "virtual disk, or a phone once your file manager has mounted it. Music "
            "can be converted on the way across, and the converted copy is never "
            "kept on this computer."
        )
        blurb.setObjectName("EmptyBody")
        blurb.setWordWrap(True)
        right.addWidget(blurb)

        form = QFormLayout()
        form.setSpacing(9)
        self.name = QLineEdit()
        form.addRow("Name", self.name)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        self.path = QLineEdit()
        self.path.setPlaceholderText("/run/media/you/USB-DRIVE/Music")
        path_row.addWidget(self.path, 1)
        browse = QPushButton("Browse…")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        holder_path = QWidget()
        holder_path.setLayout(path_row)
        form.addRow("Folder", holder_path)

        detected_row = QHBoxLayout()
        detected_row.setContentsMargins(0, 0, 0, 0)
        self.detected = QComboBox()
        self.detected.setToolTip("Drives and phones currently mounted")
        self.detected.currentIndexChanged.connect(self._pick_detected)
        detected_row.addWidget(self.detected, 1)
        look = QPushButton("Look again")
        look.setCursor(Qt.CursorShape.PointingHandCursor)
        look.clicked.connect(self._probe)
        detected_row.addWidget(look)
        detected_holder = QWidget()
        detected_holder.setLayout(detected_row)
        form.addRow("Detected", detected_holder)

        self.fmt = QComboBox()
        for key, (label, _ext, _codec) in FORMATS.items():
            self.fmt.addItem(label, key)
        self.fmt.currentIndexChanged.connect(self._format_changed)
        form.addRow("Convert to", self.fmt)

        self.quality = QComboBox()
        form.addRow("Quality", self.quality)

        self.selection = QComboBox()
        form.addRow("What to send", self.selection)

        self.layout_box = QComboBox()
        self.layout_box.addItem("Same folders as my library", "mirror")
        self.layout_box.addItem("Artist / Album / Track", "artist/album")
        self.layout_box.addItem("All files in one folder", "flat")
        form.addRow("Folder layout", self.layout_box)

        self.artwork = QComboBox()
        self.artwork.addItem("Embed it, and copy cover.jpg too", "both")
        self.artwork.addItem("Embed it in each file", "embed")
        self.artwork.addItem("Copy cover.jpg beside the tracks", "copy")
        self.artwork.addItem("Don't send artwork", "none")
        form.addRow("Album art", self.artwork)

        self.lyrics = QComboBox()
        self.lyrics.addItem("Copy .lrc files beside the tracks", "copy")
        self.lyrics.addItem("Embed them in each file", "embed")
        self.lyrics.addItem("Both", "both")
        self.lyrics.addItem("Don't send lyrics", "none")
        form.addRow("Lyrics", self.lyrics)

        self.delete_extra = QCheckBox("Delete files on the device that are not on the list")
        form.addRow("", self.delete_extra)
        self.sync_on_start = QCheckBox("Sync automatically when Aeris starts")
        self.sync_on_start.setToolTip(
            "Runs quietly a few seconds after launch, while the device is mounted. "
            "Files are copied and converted, but nothing is ever deleted unattended — "
            "press Sync once to apply the deletions.")
        form.addRow("", self.sync_on_start)
        right.addLayout(form)

        buttons = QHBoxLayout()
        save = QPushButton("Save")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self.save_target)
        buttons.addWidget(save)
        self.preview_button = QPushButton("What would happen?")
        self.preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_button.clicked.connect(self.preview)
        buttons.addWidget(self.preview_button)
        buttons.addStretch(1)
        self.sync_button = QPushButton("  Sync now")
        self.sync_button.setObjectName("Primary")
        self.sync_button.setIcon(icon("sync", 16, "#08111d"))
        self.sync_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_button.clicked.connect(self.sync_now)
        buttons.addWidget(self.sync_button)
        right.addLayout(buttons)

        self.phone_notice = QLabel("")
        self.phone_notice.setObjectName("PhoneNotice")
        self.phone_notice.setWordWrap(True)
        self.phone_notice.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.phone_notice.hide()
        right.addWidget(self.phone_notice)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        right.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setObjectName("HintLabel")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        right.addStretch(1)

        holder2 = QWidget()
        holder2.setLayout(right)
        row.addWidget(holder2, 1)
        self._format_changed()

        self.finder = DeviceFinder(self)
        self.finder.found.connect(self._devices_found)
        self._mounts: list[str] = []

    # ------------------------------------------------------------------- data

    def reload(self) -> None:
        chosen = self._editing.get("id") if self._editing else None
        self.list.blockSignals(True)
        self.list.clear()
        for target in self.library.sync_targets():
            item = QListWidgetItem(f"  {target['name']}\n      {target['fmt']} · "
                                   f"{Path(target['path']).name or target['path']}")
            item.setData(TARGET_ROLE, target["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)
        for row in range(self.list.count()):
            if self.list.item(row).data(TARGET_ROLE) == chosen:
                self.list.setCurrentRow(row)
                break
        else:
            if self.list.count():
                self.list.setCurrentRow(0)
            else:
                self._editing = None
                self.title.setText("No destination yet")
        self._refresh_detected()
        self._refresh_selections()

    def _probe(self) -> None:
        self.finder.refresh()

    def _refresh_detected(self) -> None:
        self.detected.blockSignals(True)
        self.detected.clear()
        self.detected.addItem("— choose —", "")
        for path in self._mounts:
            self.detected.addItem(path, path)
        self.detected.blockSignals(False)
        self._probe()

    def _devices_found(self, mounts: list, phones: list, gvfs: bool, hint: str) -> None:
        """A phone libmtp can see but nothing has turned into a folder."""
        self._mounts = list(mounts)
        current = self.detected.currentData()
        self.detected.blockSignals(True)
        self.detected.clear()
        self.detected.addItem("— choose —", "")
        for path in self._mounts:
            self.detected.addItem(path, path)
        if current:
            index = self.detected.findData(current)
            if index >= 0:
                self.detected.setCurrentIndex(index)
        self.detected.blockSignals(False)

        reachable = [p for p in self._mounts if "mtp" in p.lower()]
        if not phones or reachable:
            self.phone_notice.hide()
            return
        names = ", ".join(phones)
        if gvfs:
            self.phone_notice.setText(
                f"{names} is plugged in but not mounted yet. Open it once in your file "
                f"manager — that mounts it — then press Look again.")
        else:
            self.phone_notice.setText(
                f"{names} is plugged in, but nothing on this system turns it into a "
                f"folder.\n\nPhones speak MTP, which is not a filesystem. Dolphin can "
                f"browse it because KDE talks MTP itself, but that only works inside "
                f"KDE — every other program, Aeris included, needs the gvfs bridge:\n\n"
                f"    {hint}\n\n"
                f"Then reconnect the phone, open it once in your file manager, and press "
                f"Look again. It will appear here as a folder.")
        self.phone_notice.show()

    def _refresh_selections(self) -> None:
        current = self.selection.currentData()
        self.selection.blockSignals(True)
        self.selection.clear()
        self.selection.addItem("Liked songs", "liked")
        self.selection.addItem("Everything", "all")
        for playlist in self.library.playlists():
            self.selection.addItem(f"Playlist: {playlist['name']}", f"playlist:{playlist['id']}")
        for artist in self.library.artists():
            self.selection.addItem(f"Artist: {artist['name']}", f"artist:{artist['name']}")
        self.selection.blockSignals(False)
        if current:
            index = self.selection.findData(current)
            if index >= 0:
                self.selection.setCurrentIndex(index)

    def _selected(self, item, _previous=None) -> None:
        if item is None:
            return
        target = self.library.sync_target(int(item.data(TARGET_ROLE)))
        if target is None:
            return
        self._editing = target
        self.title.setText(target["name"])
        self.name.setText(target["name"])
        self.path.setText(target["path"])
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(target["fmt"])))
        self._format_changed()
        index = self.quality.findText(target["quality"])
        if index >= 0:
            self.quality.setCurrentIndex(index)
        self._refresh_selections()
        index = self.selection.findData(target["selection"])
        self.selection.setCurrentIndex(max(0, index))
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(target["layout"])))
        self.artwork.setCurrentIndex(max(0, self.artwork.findData(target.get("artwork", "both"))))
        self.lyrics.setCurrentIndex(max(0, self.lyrics.findData(target.get("lyrics", "copy"))))
        self.delete_extra.setChecked(bool(target["delete_extra"]))
        self.sync_on_start.setChecked(bool(target.get("sync_on_start", 0)))
        last = target["last_sync"]
        self.status.setText("Never synced" if not last else
                            f"Last synced {__import__('time').strftime('%d %b %H:%M', __import__('time').localtime(last))}")

    def _format_changed(self) -> None:
        fmt = self.fmt.currentData() or "copy"
        self._warn_about_art(fmt)
        self.quality.clear()
        for value in QUALITIES.get(fmt, ["default"]):
            self.quality.addItem(value)
        default = DEFAULT_QUALITY.get(fmt, "default")
        index = self.quality.findText(default)
        if index >= 0:
            self.quality.setCurrentIndex(index)
        self.quality.setEnabled(fmt not in ("copy", "flac"))

    def _warn_about_art(self, fmt: str) -> None:
        """Opus and Ogg cannot hold embedded art, so say so rather than fail quietly."""
        from .converter import can_embed_art
        if fmt not in ("copy", "") and not can_embed_art(fmt):
            self.artwork.setToolTip(
                f"{fmt.upper()} files cannot carry embedded artwork, so Aeris copies "
                f"cover.jpg beside the tracks instead — which is what phone players read.")
        else:
            self.artwork.setToolTip("")

    def _pick_detected(self) -> None:
        path = self.detected.currentData()
        if path:
            self.path.setText(str(Path(path) / "Music"))

    def _browse(self) -> None:
        start = self.path.text().strip() or "/run/media"
        folder = QFileDialog.getExistingDirectory(self, "Where should the music go?", start)
        if folder:
            self.path.setText(folder)

    # ---------------------------------------------------------------- actions

    def new_target(self) -> None:
        self._editing = {}
        self.title.setText("New destination")
        self.name.setText("")
        self.path.setText("")
        self.fmt.setCurrentIndex(0)
        self.artwork.setCurrentIndex(0)
        self.lyrics.setCurrentIndex(0)
        self.delete_extra.setChecked(False)
        self.sync_on_start.setChecked(False)
        self.status.setText("")

    def _collect(self) -> dict:
        target = dict(self._editing or {})
        target.update({
            "name": self.name.text().strip() or "Untitled",
            "path": self.path.text().strip(),
            "fmt": self.fmt.currentData() or "copy",
            "quality": self.quality.currentText() or "default",
            "selection": self.selection.currentData() or "liked",
            "layout": self.layout_box.currentData() or "mirror",
            "artwork": self.artwork.currentData() or "both",
            "lyrics": self.lyrics.currentData() or "copy",
            "delete_extra": 1 if self.delete_extra.isChecked() else 0,
            "sync_on_start": 1 if self.sync_on_start.isChecked() else 0,
        })
        return target

    def save_target(self) -> dict | None:
        target = self._collect()
        if not target["path"]:
            self.status.setText("Choose a folder to sync to first.")
            return None
        target_id = self.library.save_sync_target(target)
        target["id"] = target_id
        self._editing = self.library.sync_target(target_id)
        self.reload()
        self.status.setText("Saved.")
        return self._editing

    def preview(self) -> None:
        target = self.save_target()
        if target is None:
            return
        plan = build_plan(self.library, target)
        if plan.error:
            self.status.setText(plan.error)
            return
        lines = [plan.summary()]
        if plan.removals:
            names = ", ".join(p.name for p in plan.removals[:4])
            lines.append(f"Would delete: {names}"
                         f"{' and more' if len(plan.removals) > 4 else ''}")
        self.status.setText("\n".join(lines))

    def sync_now(self) -> None:
        target = self.save_target()
        if target is None:
            return
        if self.controller.busy:
            self.status.setText("A sync is already running.")
            return
        plan = build_plan(self.library, target)
        if plan.error:
            self.status.setText(plan.error)
            return
        if plan.removals:
            answer = QMessageBox.question(
                self, "Delete files on the device?",
                f"{len(plan.removals)} file(s) on {target['name']} are not on the sync "
                f"list and will be deleted.\n\nGo ahead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.progress.setRange(0, 0)
        self.progress.show()
        self.sync_button.setEnabled(False)
        self.status.setText(f"Syncing to {target['name']}…")
        self.controller.start(target)

    def run_startup_syncs(self) -> None:
        """Quietly sync every destination flagged for startup.

        Unattended runs never delete: the plan is built with deletions off, so
        a device whose `delete_extra` is set stays untouched until someone
        presses Sync in person.
        """
        self._startup_queue = [dict(t) for t in self.library.sync_targets()
                               if t.get("sync_on_start")]
        self._start_next_startup()

    def _start_next_startup(self) -> None:
        """Hand the next startup destination straight to the worker.

        The plan is deliberately not worked out here first. Building one means
        stat-ing every file at the far end — over gvfs for a phone, which is a
        network round trip each — and starting an ffprobe for every album that
        still wants a cover. On the interface thread, moments after the window
        opens, that is a freeze. The worker builds its own plan anyway, and
        reports nothing to do as quickly as this could have found out.
        """
        if not self._startup_queue:
            return
        target = self._startup_queue.pop(0)
        self.sync_button.setEnabled(False)
        self.status.setText(f"Startup sync to {target['name']}…")
        # Unattended runs never delete.
        if not self.controller.start(dict(target, delete_extra=0)):
            self.sync_button.setEnabled(True)
            QTimer.singleShot(2000, self._start_next_startup)

    def _on_progress(self, done: int, total: int, label: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.show()       # a startup sync shows it only once it starts
        self.status.setText(f"{done}/{total} · {label}")

    def _on_finished(self, copied: int, covers: int, deleted: int, failed: int,
                     error: str) -> None:
        self.progress.hide()
        self.sync_button.setEnabled(True)
        if error:
            self.status.setText(error)
        else:
            bits = []
            if copied:
                bits.append(f"{copied} sent")
            if covers:
                bits.append(f"{covers} cover{'s' if covers != 1 else ''} added")
            if deleted:
                bits.append(f"{deleted} deleted")
            if failed:
                bits.append(f"{failed} failed")
            # Reload first: it re-selects the target, which rewrites the status line.
            self.reload()
            self.status.setText(("Done — " + ", ".join(bits)) if bits else "Already up to date")
        if self._startup_queue:
            QTimer.singleShot(1500, self._start_next_startup)
            self.status.setText(self.status.text() + " · next destination coming up")

    def shutdown(self) -> None:
        self._startup_queue = []
        self.controller.stop()

    def _menu(self, where: QPoint) -> None:
        item = self.list.itemAt(where)
        if item is None:
            return
        target_id = int(item.data(TARGET_ROLE))
        menu = QMenu(self)
        sync = menu.addAction("Sync now")
        sync.triggered.connect(self.sync_now)
        menu.addSeparator()
        delete = menu.addAction("Remove this destination")
        delete.triggered.connect(lambda: (self.library.delete_sync_target(target_id),
                                          self.reload()))
        menu.exec(self.list.viewport().mapToGlobal(where))
