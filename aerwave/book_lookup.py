"""What an audiobook actually is: its name, its author, and a cover.

Discord fetches presence images from its own servers, not from your machine, so
the cover Aerwave already has on disk is no use there — it needs a public address.
Apple's audiobook search answers that: no key, no account, and it indexes
audiobook editions rather than the printed book, so the art matches the thing
being listened to.

Only the book's name goes out, and only when something asks — showing presence,
or tidying a shelf.

Series are not in the catalogue at all, so they come from the shelf itself:
folders are named "Author - Series 3 - The Title" often enough that the shape
can be read back off them.
"""

import json
import re
from dataclasses import dataclass
import urllib.parse
import urllib.request
from pathlib import Path

SEARCH = "https://itunes.apple.com/search"
TIMEOUT = 8
AGENT = "Aerwave/3.0 (desktop media player)"
GOOD_ENOUGH = 0.4

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_EDITION = re.compile(r"\((?:un)?abridged\)|\[[^\]]*\]|\(\d+\s+of\s+\d+\)", re.I)
# Words that say nothing about which book this is.
_NOISE = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "by", "with",
          "book", "novel", "audiobook", "unabridged", "abridged", "edition", "series",
          "vol", "volume", "part", "read", "narrated", "cosmere"}


_tag_titles: dict = {}


def shelf_names(book: dict) -> tuple[str, str]:
    """The folder a book came from and its file name, with no suffix.

    Everything about what a book is called is read off these two strings, so
    where they come from matters more than anything else here. Once Aerwave has
    renamed a book, the name on disk is Aerwave's own previous answer; reading it
    back is how an earlier version of this turned "Mistborn #1 - The Final
    Empire" into "The Final Empire" and then into plain "Mistborn". So a book
    that has been renamed is read from `origin` instead — the name it had when
    it arrived, written down once and never again. A folder is recorded with a
    trailing slash, since by then there is no folder left to ask.
    """
    origin = (book.get("origin") or "").strip()
    if origin:
        source = Path(origin.rstrip("/"))
        if origin.endswith("/"):
            return source.name, ""
        return source.parent.name, source.stem
    source = Path(book.get("path") or "")
    if not source.name:
        return "", ""
    if source.is_dir():
        return source.name, ""
    return source.parent.name, source.stem


def tag_title(book: dict) -> str:
    """What the file itself says it is called.

    Deliberately not the title held in the library: that one is what tidying
    writes, and a lookup that reads its own previous answer will wander. Once
    tidying has written the tags too, the answer comes from `origin_tag` — what
    they said before Aerwave ever touched them — for exactly the same reason.
    """
    if book.get("tidied"):
        return (book.get("origin_tag") or "").strip()
    path = book.get("path") or ""
    source = Path(path)
    if not path or source.is_dir():
        return ""
    try:
        key = (path, source.stat().st_mtime_ns)
    except OSError:
        return ""
    if key in _tag_titles:
        return _tag_titles[key]
    title = ""
    try:
        from mutagen import File                    # noqa: PLC0415  (optional work)
        tags = File(path, easy=True)
        if tags is not None:
            for field in ("title", "album"):
                value = (tags.get(field) or [""])[0]
                if value and str(value).strip():
                    title = str(value).strip()
                    break
    except Exception:                               # noqa: BLE001  (unreadable file)
        title = ""
    if len(_tag_titles) > 2000:
        _tag_titles.clear()
    _tag_titles[key] = title
    return title


def normalise(text: str) -> str:
    """Lowercase words only, so spacing and punctuation stop mattering."""
    return " ".join(_WORD.findall((text or "").lower()))


def _tokens(text: str) -> set:
    found = set()
    for word in _WORD.findall((text or "").lower()):
        if word.isdigit():
            found.add(str(int(word)))
        elif word not in _NOISE and len(word) > 1:
            found.add(word)
    return found


def _numbers(text: str) -> set:
    return {int(n) for n in re.findall(r"(?<!\d)\d{1,3}(?!\d)", text or "")}


def clean_title(title: str) -> str:
    """A title worth searching for: no edition notes, no trailing punctuation."""
    return re.sub(r"\s{2,}", " ", _EDITION.sub(" ", title or "")).strip(" -:,")


def name_hints(book: dict) -> set:
    """The words that say which book this is, rather than which series.

    Folders are named "Author - Series 3 - The Title" often enough that the
    part after the last dash is the book's own name. Everything a result must
    share is in here: two books in one series have the series in common, and
    matching on that alone would put Dawnshard's cover on Edgedancer.
    """
    folder, stem = shelf_names(book)
    hints: set = set()
    for name in (folder, stem):
        tail = re.split(r"\s[-\u2013]\s", clean_title(name))[-1]
        hints |= _tokens(tail)
    # A tag title counts only when it reads like a title rather than a path:
    # the long ones are the very blobs whose series words caused the trouble.
    tagged = _tokens(clean_title(tag_title(book)))
    if 0 < len(tagged) <= 6:
        hints |= tagged
    return hints


def identity(book: dict) -> tuple[set, set, str]:
    """What names this book: its tags, and the folder it came from.

    Audiobook tags lie often enough — plenty of files carry the first chapter's
    name as the title — that the folder and file names have to count too.
    """
    folder, stem = shelf_names(book)
    written = f"{tag_title(book)} {book.get('series', '')} {folder} {stem}"
    return _tokens(written), _numbers(written), folder


def _score(name: str, artist: str, wanted: set, numbers: set,
           hints: set | None = None) -> float:
    """How well one search result matches the book, from 0 to 1.

    Both directions matter. Missing half the book's words means the wrong book;
    carrying words the book has never heard of — "dramatized", "adaptation" —
    means the wrong edition of the right one, which would show the wrong cover.
    """
    found = _tokens(name)
    if not found or not wanted:
        return 0.0
    result_numbers = _numbers(name)
    if numbers and result_numbers and not (numbers & result_numbers):
        return 0.0
    shared = wanted & found
    if not shared:
        return 0.0
    if hints and not (hints & found):
        return 0.0                         # shares the series, not the book
    precision = len(shared) / len(found)
    recall = len(shared) / min(len(wanted), max(3, len(found) + 1))
    score = (precision * 2 + recall) / 3
    if _tokens(artist) & wanted:
        score = min(1.0, score + 0.1)
    return score


@dataclass
class Match:
    """What the catalogue knows about a book, or nothing much."""

    name: str = ""
    author: str = ""
    year: str = ""
    genre: str = ""
    art: str = ""
    score: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.name)


_SERIES_TAIL = re.compile(r"^(?P<series>.+?)\s*#?(?P<number>\d+(?:\.\d+)?)$")


def series_of(book: dict) -> tuple[str, str]:
    """The series a book belongs to and its place in it, read off the shelf.

    Two shapes cover almost everything: a file called "Mistborn #4 - The Alloy
    of Law", and a folder called "Sanderson, Brandon - Cosmere - The Stormlight
    Archive 1 - The Way of Kings", where the part before the title says which
    series and which number.
    """
    tagged = (book.get("series") or "").strip()
    folder, stem = shelf_names(book)
    for name in (stem, folder):
        parts = [part.strip() for part in re.split(r"\s[-\u2013]\s", clean_title(name))
                 if part.strip()]
        if len(parts) < 2:
            continue
        found = _SERIES_TAIL.match(parts[-2])
        if found:
            return found.group("series").strip(" -#"), found.group("number")
    return tagged, ""


_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}\s*[-.\u2013]?\s+")
# File names that say nothing: the tail of the folder is better than these.
_GENERIC = re.compile(r"^(audio ?book|book|part|track|disc|cd|chapter|file|untitled|"
                      r"unknown|output|final|new|copy|\d+)\s*\d*$", re.I)
_SURNAME_FIRST = re.compile(r"^\s*([^,]+),\s*([^,]+?)\s*$")


def shelf_title(book: dict) -> str:
    """What the shelf calls this book — the most reliable thing about it.

    Tags get written by whoever made the file and are often wrong or empty; the
    name is what a person typed while filing it away. "Mistborn #4 - The Alloy
    of Law" and ".../The Stormlight Archive 1 - The Way of Kings" both end with
    the title, "01 The Way of Kings" just needs its track number taken off, and
    "Oathbringer (Unabridged)" needs its edition note dropped.

    A name that says nothing — "audiobook", "part 2", "output" — is passed over
    in favour of the folder it sits in.
    """
    folder, stem = shelf_names(book)
    for name in (stem, folder):
        cleaned = clean_title(name)
        if not cleaned:
            continue
        tail = re.split(r"\s[-\u2013]\s", cleaned)[-1].strip()
        tail = _LEADING_NUMBER.sub("", tail).strip()
        if tail and not _SERIES_TAIL.match(tail) and not _GENERIC.match(tail):
            return tail
    return ""


def tidy_author(name: str) -> str:
    """"Sanderson, Brandon" is a sorting key, not a name."""
    name = (name or "").strip()
    found = _SURNAME_FIRST.match(name)
    if found and " " not in found.group(1).strip():
        return f"{found.group(2).strip()} {found.group(1).strip()}"
    return name


def shelf_author(book: dict) -> str:
    """An author's name read off the folder, when the tags have none."""
    folder, _stem = shelf_names(book)
    first = re.split(r"\s[-\u2013]\s", clean_title(folder))[0]
    flipped = tidy_author(first)
    return flipped if flipped != first else ""


def _search(term: str, limit: int = 8, media: str = "audiobook") -> list:
    query = urllib.parse.urlencode({"term": term[:180], "media": media, "limit": limit})
    request = urllib.request.Request(f"{SEARCH}?{query}", headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response).get("results", []) or []
    except Exception:                      # noqa: BLE001  (offline, rate limited, changed)
        return []


def _big(url: str, size: int = 600) -> str:
    """Apple hands back a thumbnail; the size is just part of the filename."""
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.\\1", url or "")


def search_terms(book: dict) -> list[str]:
    """The queries worth trying for a book, best first."""
    shelf = shelf_title(book)
    tagged = clean_title(tag_title(book))
    folder, _stem = shelf_names(book)
    author = (book.get("author") or "").strip() or shelf_author(book)
    terms, seen = [], set()
    for term in (f"{shelf} {author}".strip(), f"{tagged} {author}".strip(),
                 clean_title(folder), shelf, tagged):
        term = " ".join(term.split())
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def candidates(book: dict, query: str = "", limit: int = 24) -> list[Match]:
    """Every cover the catalogue can offer for a book, best guess first.

    `look_up` answers with the one result it is confident about, and refuses
    anything it is not — which is right when the answer is going to be used
    without anyone seeing it. Choosing a cover by hand is the opposite case:
    the point is to be shown the near misses and the wrong editions too, and
    decide. So the score here sorts rather than vetoes, and a typed query is
    taken at its word.
    """
    typed = " ".join(query.split())
    wanted, numbers, _folder = identity(book)
    terms = [typed] if typed else search_terms(book)
    found: dict[str, tuple[int, Match]] = {}
    position = 0
    for term in terms[:3]:
        # Audiobook editions first, then the printed book: plenty of titles are
        # only in one of the two catalogues, and either cover is a real cover.
        for media in ("audiobook", "ebook"):
            for result in _search(term, limit=12, media=media):
                art = _big(result.get("artworkUrl100") or "")
                if not art.startswith("https://"):
                    continue
                position += 1
                name = result.get("collectionName") or result.get("trackName") or ""
                artist = (result.get("artistName") or "").strip()
                # A typed query is the catalogue's question to answer, so its
                # own ranking is kept; a derived one is ours, and scored.
                score = 0.5 if typed else _score(name, artist, wanted, numbers)
                if art in found and found[art][1].score >= score:
                    continue
                found[art] = (position, Match(
                    name=clean_title(name), author=artist,
                    year=str(result.get("releaseDate") or "")[:4],
                    genre=(result.get("primaryGenreName") or "").strip(),
                    art=art, score=round(score, 3)))
        if len(found) >= limit:
            break
    best = sorted(found.values(), key=lambda pair: (-pair[1].score, pair[0]))
    return [match for _position, match in best[:limit]]


def lookup(book: dict) -> tuple[str, str]:
    """The older form: just the name and the cover, for Rich Presence."""
    found = look_up(book)
    return found.name, found.art


def own_name_hints(book: dict) -> set:
    """The words from the book's own name, without the folder's.

    `name_hints` counts the folder too, which is usually right — it carries the
    series or the author. But a book sitting in a folder called "Audiobooks" or
    "Nowhere" then matches whatever the catalogue lists under that name, and a
    wrong picture on the shelf is far more conspicuous than a wrong word in a
    status line. So the picture is held to the stricter question: does this
    result share a word with what the book itself is called?
    """
    _folder, stem = shelf_names(book)
    hints = _tokens(re.split(r"\s[-\u2013]\s", clean_title(stem))[-1])
    hints |= _tokens(clean_title(book.get("title") or ""))
    tagged = _tokens(clean_title(tag_title(book)))
    if 0 < len(tagged) <= 6:
        hints |= tagged
    return hints


def cover_url(book: dict) -> str:
    """A public address for this book's artwork, or "" if none is sure enough."""
    strict = own_name_hints(book)
    return look_up(book, hints=strict).art if strict else ""


def look_up(book: dict, hints: set | None = None) -> Match:
    """What this audiobook really is, as far as the catalogue knows.

    The name is worth as much as the picture: tags carry edition notes nobody
    wants to read in a status line — *(Unabridged)*, *Book 3 of 7* — and
    sometimes name the first chapter instead of the book.

    `hints` is the set of words a result must share with the book; the default
    is everything that names it, folder included.
    """
    wanted, numbers, folder = identity(book)
    if not wanted:
        return Match()
    if hints is None:
        hints = name_hints(book)
    shelf = shelf_title(book)
    tagged = clean_title(tag_title(book))
    author = (book.get("author") or "").strip() or shelf_author(book)
    terms = [f"{shelf} {author}".strip(), f"{tagged} {author}".strip(),
             clean_title(folder)]
    best, best_score = Match(), GOOD_ENOUGH
    seen: set[str] = set()
    for term in terms:
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        for result in _search(term):
            name = result.get("collectionName") or result.get("trackName") or ""
            score = _score(name, result.get("artistName") or "", wanted, numbers, hints)
            if score > best_score:
                art = _big(result.get("artworkUrl100") or "")
                best = Match(name=clean_title(name),
                             author=(result.get("artistName") or "").strip(),
                             year=str(result.get("releaseDate") or "")[:4],
                             genre=(result.get("primaryGenreName") or "").strip(),
                             art=art if art.startswith("https://") else "",
                             score=round(score, 2))
                best_score = score
        if best:
            break                          # the book's own title is the better query
    return best
