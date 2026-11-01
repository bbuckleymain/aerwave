"""Tidying a shelf: what each book should be called, and where it should live.

Names are read off file and folder names, because that is where the truth
usually is — a file name is what a person typed while filing the book away,
where a tag is whoever-made-the-file's guess and a catalogue is a different
edition's marketing. The catalogue is still worth having for the author, the
year and the cover.

Renaming a book is therefore an awkward thing to do: it destroys the evidence
the new name was read from. An earlier version of this did exactly that, and on
its second pass had only its own answer left to read — by which point "Mistborn
#1 - The Final Empire" had become "The Final Empire", and then plain "Mistborn",
which is what the catalogue calls the first book of that series.

The fix is not to be careful, it is to keep the evidence. The moment a book is
about to be renamed or retagged, the name and tag it arrived with are written
into `books.origin` and `books.origin_tag` and never written again, and every
later pass reads those instead of the disk. Tidying the same shelf twice now
reaches the same answer, which is the property that makes it safe at all.

Everything that touches a file is also written into `book_moves`, so a whole
run can be put back.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import book_files, book_lookup
from .book_scanner import AUDIO_EXTENSIONS

TAG_FIELDS = ("title", "author", "narrator", "series")


@dataclass
class Change:
    """Everything one book needs putting right."""

    book_id: int
    path: str
    root: str = ""
    title: str = ""
    fields: dict = field(default_factory=dict)     # name -> (before, after)
    target: str = ""                               # where the files should go
    tags: dict = field(default_factory=dict)       # tag field -> what it should say
    folder_book: bool = False
    note: str = ""
    problem: str = ""

    @property
    def moves(self) -> bool:
        return bool(self.target) and self.target != self.path

    @property
    def anything(self) -> bool:
        return bool(self.fields) or self.moves or bool(self.tags)

    def after(self, name: str, current: str = "") -> str:
        return self.fields[name][1] if name in self.fields else current

    def lines(self, *, rename: bool = False, retag: bool = False) -> list[str]:
        """How this change reads in the dialog, given what has been asked for."""
        out = [f"{name}:  {before or '—'}   →   {after}"
               for name, (before, after) in self.fields.items()]
        if rename and self.moves:
            here, there = Path(self.path), Path(self.target)
            out.append(f"file:  {here.parent.name}/{here.name}   →   "
                       f"{there.parent.name}/{there.name}")
        if retag and self.tags:
            out.append("tags:  " + ", ".join(sorted(self.tags)) + "  written into the file")
        if self.note:
            out.append(self.note)
        if rename and self.problem:
            out.append(f"cannot move: {self.problem}")
        return out


def _proposed(book: dict, match, series: str) -> dict:
    """What each field should say, given what the catalogue and shelf know."""
    shelf = book_lookup.shelf_title(book)
    title = (shelf or match.name
             or book_lookup.clean_title(book_lookup.tag_title(book))
             or (book.get("title") or "").strip())
    # Catalogues often append the series: "Edgedancer (Stormlight Archive)".
    if series and title.lower().endswith(f"({series.lower()})"):
        title = title[:-(len(series) + 2)].strip()
    author = (match.author or book_lookup.tidy_author(book.get("author") or "")
              or book_lookup.shelf_author(book))
    wanted = {
        "title": title.strip(),
        "author": author.strip(),
        "narrator": (book.get("narrator") or "").strip(),   # no catalogue carries it
        "series": series.strip(),
    }
    return {name: value for name, value in wanted.items() if value}


def plan_one(library, book: dict, *, rename: bool = False,
             retag: bool = False) -> Change:
    """Work out what one book should be called, and where. Touches nothing."""
    source = Path(book["path"])
    change = Change(book_id=book["id"], path=book["path"], root=book.get("root") or "",
                    title=book.get("title") or source.stem,
                    folder_book=source.is_dir())
    match = book_lookup.look_up(book)
    series, number = book_lookup.series_of(book)
    values = _proposed(book, match, series)
    if not match:
        change.note = "not in the catalogue — using what the shelf says"

    for name in TAG_FIELDS:
        after = values.get(name, "")
        before = (book.get(name) or "").strip()
        if after and after != before:
            change.fields[name] = (before, after)

    title = values.get("title") or change.title
    author = values.get("author", "")
    if retag:
        change.tags = book_files.tag_gap(
            _audio_under(source),
            book_files.tag_plan(title, author, values.get("narrator", ""), series,
                                whole_book=not change.folder_book))
    if rename:
        change.target = str(_where(change, source, title, author, series, number))
    return change


def _where(change: Change, source: Path, title: str, author: str,
           series: str, number: str) -> Path:
    """The place on a tidy shelf this book belongs, or where it already is."""
    root = Path(change.root or source.parent)
    if root not in source.parents:
        change.problem = ("it is a watched folder itself" if root == source
                          else "it is not inside a watched folder")
        return source
    if not author:
        # The shelf is arranged by author, so without one there is nowhere to
        # put the book. Filing it under "Unknown" would be worse than leaving
        # it where whoever downloaded it decided it should go.
        change.problem = "nobody knows who wrote it"
        return source
    return book_files.target_for(str(root), source, title=title, author=author,
                                 series=series, number=number)


def plan(library, books: list, *, rename: bool = False, retag: bool = False) -> list:
    """What a shelf full of books should be called. Touches nothing."""
    return [plan_one(library, book, rename=rename, retag=retag) for book in books]


@dataclass
class Report:
    retitled: int = 0
    moved: int = 0
    retagged: int = 0
    pruned: int = 0
    skipped: int = 0
    batch: int = 0
    failed: list = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.retitled:
            bits.append(f"{self.retitled} set right")
        if self.moved:
            bits.append(f"{self.moved} moved")
        if self.retagged:
            bits.append(f"{self.retagged} retagged")
        if self.skipped:
            bits.append(f"{self.skipped} left where they are")
        return " · ".join(bits) or "nothing to do"


def _audio_under(source: Path) -> list[Path]:
    """The files of a book whose tags would need writing."""
    if source.is_file():
        return [source]
    try:
        return sorted(p for p in source.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    except OSError:
        return []


def _remember(library, change: Change, done: set) -> None:
    """Copy aside the evidence, once, before anything can destroy it."""
    if change.book_id in done:
        return
    done.add(change.book_id)
    origin = change.path + "/" if change.folder_book else change.path
    library.remember_origin(change.book_id, origin,
                            book_lookup.tag_title({"path": change.path}))


def apply(library, changes: list, *, rename: bool = False, retag: bool = False,
          busy: tuple = ()) -> Report:
    """Carry out the changes that were shown, and write down the ones that move.

    Library names are always safe to write. Tags and moves only happen when
    they were asked for, are skipped for the book being listened to — `busy`
    is the ids of those, since mpv is holding the file open — and are recorded
    in `book_moves` first, so the whole run can be undone.
    """
    report = Report()
    listening = {int(book_id) for book_id in busy}
    taken: set[str] = set()
    remembered: set[int] = set()
    touching = (rename or retag) and any(
        change.moves or change.tags for change in changes)
    if touching:
        report.batch = library.start_tidy_batch()

    for change in changes:
        if change.fields:
            library.set_book_meta(change.book_id,
                                  **{name: after for name, (_before, after)
                                     in change.fields.items()})
            report.retitled += 1
        if not touching:
            continue
        if change.book_id in listening:
            if change.moves or change.tags:
                report.failed.append(f"{change.title}: it is playing")
            continue
        if retag and change.tags:
            _retag(library, change, report, remembered)
        if rename and change.moves and not change.problem:
            _relocate(library, change, report, taken, remembered)
        elif rename and change.problem:
            report.skipped += 1        # the row already says why
    return report


def _retag(library, change: Change, report: Report, remembered: set) -> None:
    files = _audio_under(Path(change.path))
    if not files:
        return
    _remember(library, change, remembered)
    before, wrote = {}, False
    for target in files:
        try:
            before[str(target)] = book_files.write_tags(target, change.tags)
            wrote = True
        except Exception as problem:       # noqa: BLE001  (any unwritable file)
            report.failed.append(f"{target.name}: {problem}")
    if wrote:
        library.note_change(report.batch, change.book_id, "tags",
                            book_files.dumps(before), book_files.dumps(change.tags))
        report.retagged += 1


def _relocate(library, change: Change, report: Report, taken: set,
              remembered: set) -> None:
    source, target = Path(change.path), Path(change.target)
    if change.target in taken:
        report.failed.append(f"{change.title}: two books want the same place")
        return
    taken.add(change.target)
    _remember(library, change, remembered)
    moved: list[tuple[str, str, str]] = []
    try:
        moved = book_files.move_book(
            source, target,
            note=lambda a, b, kind: library.note_change(report.batch, change.book_id,
                                                        kind, a, b))
    except book_files.MoveError as problem:
        report.failed.append(f"{change.title}: {problem}")
        return
    if not library.move_book(change.book_id, change.path, change.target):
        report.failed.append(f"{change.title}: moved, but the index would not follow")
    for before, after, _kind in moved[1:]:
        if before.lower().endswith(".epub"):
            library.repoint_text(change.book_id, after)
    change.path = change.target
    report.moved += 1
    if change.root:
        report.pruned += book_files.prune(source.parent, Path(change.root))


def undo(library, batch: int) -> Report:
    """Put a whole run back: every file where it was, every tag as it read.

    The journal is walked newest first, which is the order a move has to be
    reversed in — a book's sidecars went after the book, so they come back
    before it. Only what actually made it home is reported to the index.
    """
    report = Report(batch=batch)
    restored: set[int] = set()
    roots: dict[int, str] = {}            # book id -> the folder it was scanned from
    emptied: list[tuple[Path, str]] = []  # folders the run made, and their root
    for row in library.tidy_changes(batch):
        if row["kind"] in ("move", "side"):
            try:
                book_files.relocate(Path(row["after"]), Path(row["before"]))
            except book_files.MoveError as problem:
                report.failed.append(str(problem))
                continue
            if row["book_id"] not in roots:
                book = library.book(row["book_id"])
                roots[row["book_id"]] = (book or {}).get("root") or ""
            emptied.append((Path(row["after"]).parent, roots[row["book_id"]]))
            if row["kind"] == "move":
                library.move_book(row["book_id"], row["after"], row["before"])
                report.moved += 1
            elif row["before"].lower().endswith(".epub"):
                library.repoint_text(row["book_id"], row["before"])
            restored.add(row["book_id"])
            continue
        trouble = False
        for path, values in book_files.loads(row["before"]).items():
            try:
                book_files.write_tags(Path(path), values)
            except Exception as problem:    # noqa: BLE001  (any unwritable file)
                report.failed.append(f"{Path(path).name}: {problem}")
                trouble = True
        if not trouble:
            restored.add(row["book_id"])
            report.retagged += 1
    # The folders the run made are no use once it is undone.
    for folder, root in emptied:
        if root:
            report.pruned += book_files.prune(folder, Path(root))
    if not report.failed:
        for book_id in restored:
            library.untidy(book_id)     # its own name is evidence again
        library.forget_tidy(batch)
    return report
