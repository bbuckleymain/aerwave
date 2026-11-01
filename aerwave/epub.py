"""The EPUB that goes with an audiobook: reading it, and lining it up.

An EPUB is a zip with a table of contents. ``META-INF/container.xml`` names the
package document; the package lists every file and the order they are read in
(the spine); and the navigation document — ``nav.xhtml`` in EPUB 3, ``toc.ncx``
in EPUB 2 — gives the chapters their names. That is all zipfile and XML work,
so Aerwave reads it here rather than taking on a dependency.

Finding the right EPUB is half the job. It is often not beside the audiobook —
audio and text usually live in separate folders — so Aerwave also looks through
the nearby folders and matches on the title, refusing any candidate whose
series number disagrees.

Lining the two up is the other half. The chapter lists are usually the same
sequence starting in different places, because an EPUB carries a cover, a title
page and a copyright notice that nobody narrates. So rather than pairing
chapters one at a time, Aerwave looks for the single shift that makes the most
titles and numbers agree — and one number, unlike a whole mapping, is something
you can correct by hand in one step.
"""

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

SUFFIXES = (".epub",)

# Sections every EPUB carries and no narrator reads out.
FRONT_MATTER = (
    "cover", "title page", "titlepage", "half title", "copyright", "contents",
    "table of contents", "colophon", "dedication", "epigraph", "imprint",
    "acknowledgements", "acknowledgments", "about the author", "also by",
    "praise for", "newsletter", "advertisement", "map", "maps",
)

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = _HEADINGS | {"p", "div", "blockquote", "li", "section", "article",
                       "figcaption", "pre", "tr", "td", "dd", "dt"}
_EMPHASIS = {"em", "i", "cite", "dfn", "var"}
_STRONG = {"strong", "b"}
_IGNORED = {"script", "style", "head", "title", "svg"}

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\n\f\v]+")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_ROMAN = re.compile(r"^[ivxlcdm]+$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_COUNTER_WORDS = ("chapter", "chapitre", "ch", "part", "pt", "section", "episode",
                  "track", "disc", "cd", "book")

# Words that say nothing about which book this is.
_NOISE = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "by",
          "book", "novel", "audiobook", "unabridged", "abridged", "epub", "retail",
          "library", "z", "lib", "vol", "volume", "series", "complete", "edition",
          "read", "narrated", "mp3", "m4b", "part", "ebook", "copy"}


# --------------------------------------------------------------- the text

class _Document(HTMLParser):
    """One XHTML file as plain paragraphs, remembering where its anchors sit.

    Only a handful of tags survive: the point is something readable in a side
    panel, not a faithful rendering of the publisher's CSS. Anchors are kept
    because a table of contents often points at ``#chapter-7`` inside a file
    that holds the whole book.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.anchors: dict[str, int] = {}
        self._parts: list[str] = []
        self._heading = ""
        self._ignoring = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag in _IGNORED:
            self._ignoring += 1
            return
        if self._ignoring:
            return
        for name, value in attrs:
            if name.lower() == "id" and value:
                # Where the block being built will land once it is flushed.
                self.anchors.setdefault(value, len(self.blocks))
        if tag in _BLOCKS:
            self._flush()
            if tag in _HEADINGS:
                self._heading = "h3"
        elif tag == "br":
            self._parts.append("<br>")
        elif tag in _EMPHASIS:
            self._parts.append("<em>")
        elif tag in _STRONG:
            self._parts.append("<strong>")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag.lower() == "br" and not self._ignoring:
            self._parts.append("<br>")
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED:
            self._ignoring = max(0, self._ignoring - 1)
            return
        if self._ignoring:
            return
        if tag in _BLOCKS:
            self._flush()
        elif tag in _EMPHASIS:
            self._parts.append("</em>")
        elif tag in _STRONG:
            self._parts.append("</strong>")

    def handle_data(self, data: str) -> None:
        if self._ignoring:
            return
        if not data.strip():
            if self._parts:
                self._parts.append(" ")   # keep words either side of a tag apart
            return
        self._parts.append(escape(data))

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = "".join(self._parts)
        self._parts = []
        heading, self._heading = self._heading, ""
        collapsed = _SPACE.sub(" ", text).strip()
        if not _TAGS.sub("", collapsed).strip():
            return                       # a wrapper with no words of its own
        tag = heading or "p"
        self.blocks.append(f"<{tag}>{collapsed}</{tag}>")


def plain_text(block: str) -> str:
    """One block with its markup taken off."""
    return _SPACE.sub(" ", _TAGS.sub("", block)).strip()


def _text_of(blocks: list[str]) -> str:
    return _TAGS.sub(" ", " ".join(blocks))


@dataclass
class Chapter:
    """One chapter of the text, ready to be shown."""

    title: str
    blocks: list = field(default_factory=list)
    words: int = 0

    @property
    def html(self) -> str:
        return "\n".join(self.blocks)

    @property
    def front_matter(self) -> bool:
        """True for the pages that exist in every book and are never narrated."""
        key = normalise(self.title)
        if any(key == name or key.startswith(name + " ") or key.endswith(" " + name)
               for name in FRONT_MATTER):
            return True
        return self.words < 120 and chapter_number(self.title) is None


@dataclass
class Ebook:
    """A parsed EPUB, or the reason it could not be read."""

    path: str = ""
    title: str = ""
    author: str = ""
    chapters: list = field(default_factory=list)
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.chapters)

    @property
    def first_chapter(self) -> int:
        """Where the book proper begins, past the cover and the copyright."""
        for index, chapter in enumerate(self.chapters):
            if not chapter.front_matter:
                return index
        return 0


# -------------------------------------------------------------- reading it

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _descendants(element, name: str):
    for child in element.iter():
        if _local(child.tag) == name:
            yield child


def _xml(archive: zipfile.ZipFile, name: str):
    return ElementTree.fromstring(archive.read(name))


def _resolve(base: str, href: str) -> str:
    href = href.split("#", 1)[0]
    from urllib.parse import unquote
    return posixpath.normpath(posixpath.join(base, unquote(href)))


def _package_name(archive: zipfile.ZipFile) -> str:
    container = _xml(archive, "META-INF/container.xml")
    for rootfile in _descendants(container, "rootfile"):
        path = rootfile.get("full-path")
        if path:
            return path
    raise ValueError("no package document")


def _toc_entries(archive: zipfile.ZipFile, base: str, manifest: dict, package) -> list:
    """The table of contents as [(file, fragment, title)], in reading order."""
    nav_name = ncx_name = ""
    for item_id, item in manifest.items():
        properties = (item.get("properties") or "").split()
        if "nav" in properties:
            nav_name = item["path"]
        if item.get("media_type") == "application/x-dtbncx+xml":
            ncx_name = item["path"]
    if not ncx_name:
        spine = next(iter(_descendants(package, "spine")), None)
        toc_id = spine.get("toc") if spine is not None else None
        if toc_id and toc_id in manifest:
            ncx_name = manifest[toc_id]["path"]

    entries: list[tuple[str, str]] = []
    source_name = ""
    if nav_name:
        try:
            entries, source_name = _nav_entries(archive, nav_name), nav_name
        except (KeyError, ElementTree.ParseError, ValueError, UnicodeDecodeError):
            entries = []
    if not entries and ncx_name:
        try:
            entries, source_name = _ncx_entries(archive, ncx_name), ncx_name
        except (KeyError, ElementTree.ParseError, ValueError, UnicodeDecodeError):
            entries = []

    # Links in a table of contents are relative to the file they are written in.
    toc_base = posixpath.dirname(source_name)
    resolved = []
    for href, title in entries:
        fragment = href.split("#", 1)[1] if "#" in href else ""
        resolved.append((_resolve(toc_base, href), fragment, title))
    return resolved


def _nav_entries(archive: zipfile.ZipFile, name: str) -> list:
    """EPUB 3: the <nav epub:type="toc"> list."""
    parser = _NavParser()
    parser.feed(archive.read(name).decode("utf-8", "replace"))
    parser.close()
    return parser.entries


class _NavParser(HTMLParser):
    """The links inside the table-of-contents nav, with their text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[tuple[str, str]] = []
        self._in_toc = False
        self._depth = 0
        self._href = ""
        self._text: list[str] = []
        self._seen_nav = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag == "nav":
            kind = attributes.get("epub:type") or attributes.get("type") or ""
            self._in_toc = ("toc" in kind.split()) or not self._seen_nav and not kind
            self._seen_nav = True
            self._depth = 0
        elif tag == "a" and self._in_toc:
            self._href = attributes.get("href", "")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "nav":
            self._in_toc = False
        elif tag == "a" and self._in_toc and self._href:
            title = _SPACE.sub(" ", "".join(self._text)).strip()
            self.entries.append((self._href, title))
            self._href = ""

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)


def _ncx_entries(archive: zipfile.ZipFile, name: str) -> list:
    """EPUB 2: navPoints in playOrder."""
    ncx = _xml(archive, name)
    entries = []
    for point in _descendants(ncx, "navpoint"):
        content = next(_descendants(point, "content"), None)
        label = next(_descendants(point, "navlabel"), None)
        if content is None or not content.get("src"):
            continue
        text = ""
        if label is not None:
            text_node = next(_descendants(label, "text"), None)
            text = (text_node.text or "").strip() if text_node is not None else ""
        entries.append((content.get("src"), _SPACE.sub(" ", text).strip()))
    return entries


def read(path) -> Ebook:
    """Parse an EPUB into chapters. Never raises — it reports instead."""
    path = str(path)
    try:
        with zipfile.ZipFile(path) as archive:
            return _read(archive, path)
    except (OSError, ValueError, KeyError, IndexError,
            zipfile.BadZipFile, ElementTree.ParseError):
        return Ebook(path=path, error="this file could not be read as an EPUB")


def _read(archive: zipfile.ZipFile, path: str) -> Ebook:
    package_name = _package_name(archive)
    package = _xml(archive, package_name)
    base = posixpath.dirname(package_name)

    manifest: dict[str, dict] = {}
    for item in _descendants(package, "item"):
        item_id, href = item.get("id"), item.get("href")
        if not item_id or not href:
            continue
        manifest[item_id] = {"path": _resolve(base, href),
                             "media_type": (item.get("media-type") or "").lower(),
                             "properties": item.get("properties") or ""}

    spine_element = next(_descendants(package, "spine"), None)
    spine: list[str] = []
    if spine_element is not None:
        for itemref in _descendants(spine_element, "itemref"):
            idref = itemref.get("idref")
            if idref in manifest and (itemref.get("linear") or "yes").lower() != "no":
                spine.append(manifest[idref]["path"])

    titles: dict[str, list] = {}
    order: list[str] = []
    for file_name, fragment, title in _toc_entries(archive, base, manifest, package):
        titles.setdefault(file_name, []).append((fragment, title))
        order.append(file_name)
    if not spine:
        # A package with no usable spine: fall back to the contents' own order.
        spine = list(dict.fromkeys(order))

    chapters: list[Chapter] = []
    for file_name in spine:
        try:
            source = archive.read(file_name).decode("utf-8", "replace")
        except (KeyError, OSError):
            continue
        document = _Document()
        try:
            document.feed(source)
            document.close()
        except (AssertionError, ValueError):
            continue
        if not document.blocks:
            continue
        chapters.extend(_split(document, titles.get(file_name, [])))

    for number, chapter in enumerate(chapters, 1):
        if not chapter.title:
            chapter.title = f"Section {number}"

    meta_title = _meta(package, "title")
    return Ebook(path=path, title=meta_title, author=_meta(package, "creator"),
                 chapters=chapters)


def _meta(package, name: str) -> str:
    for element in _descendants(package, name):
        if (element.text or "").strip():
            return _SPACE.sub(" ", element.text).strip()
    return ""


def _split(document: _Document, entries: list) -> list:
    """One file becomes one chapter — or several, when it holds several."""
    marks = []
    for fragment, title in entries:
        marks.append((document.anchors.get(fragment, 0) if fragment else 0, title))
    marks.sort(key=lambda pair: pair[0])
    if len({index for index, _title in marks}) > 1:
        return _pieces(document.blocks, marks)

    title = marks[0][1] if marks else ""
    # Some converters throw the chapter names away but leave their anchors
    # behind, which turns a whole book into one nameless lump. Cut on those.
    if not title and _words(document.blocks) > 3000:
        anonymous = _anchor_marks(document)
        if anonymous:
            return _pieces(document.blocks, anonymous)
    return [_chapter(title or _first_heading(document.blocks), document.blocks)]


def _anchor_marks(document: _Document) -> list:
    """Anchors worth cutting at: enough of them, far enough apart."""
    indexes = sorted({index for index in document.anchors.values() if index > 0})
    if not indexes:
        return []
    marks = [(0, "")] + [(index, "") for index in indexes]
    if len(marks) > 300:
        return []                    # every paragraph is tagged; that is not a chapter list
    if _words(document.blocks) / len(marks) < 400:
        return []                    # the pieces would be too small to be chapters
    return marks


def _pieces(blocks: list, marks: list) -> list:
    pieces = []
    if marks[0][0] > 0:
        pieces.append((0, marks[0][0], ""))
    for number, (start, title) in enumerate(marks):
        end = marks[number + 1][0] if number + 1 < len(marks) else len(blocks)
        pieces.append((start, end, title))
    chapters = []
    for start, end, title in pieces:
        chunk = blocks[start:end]
        if chunk:
            chapters.append(_chapter(title or _first_heading(chunk), chunk))
    return chapters


def _words(blocks: list) -> int:
    return len(_text_of(blocks).split())


def _chapter(title: str, blocks: list) -> Chapter:
    return Chapter(title=(title or "").strip(), blocks=list(blocks), words=_words(blocks))


def _first_heading(blocks: list) -> str:
    for block in blocks[:4]:
        if block.startswith("<h3>"):
            return _TAGS.sub("", block).strip()
    return ""


# ------------------------------------------------------------------- lines

_SENTENCE = re.compile(r'(?<=[.!?\u2026])["\u201d\u2019\')\]]*\s+')
_ABBREVIATIONS = ("mr", "mrs", "ms", "dr", "st", "prof", "sgt", "lt", "capt",
                  "rev", "hon", "jr", "sr", "vs", "etc", "no", "fig")
MIN_LINE_WORDS = 4


@dataclass
class Line:
    """One line of a chapter, as it is shown and as it is timed.

    `words` and `start_word` are counted in normalised words — the same units
    the matcher and the heard timings use.
    """

    text: str
    words: int = 0
    start_word: int = 0        # words of this chapter before this line
    heading: bool = False
    blank: bool = False        # the gap between paragraphs


def _sentences(text: str) -> list:
    """Break a paragraph into sentences, without falling for 'Mr. Smith'."""
    parts = _SENTENCE.split(text)
    sentences: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        previous = sentences[-1] if sentences else ""
        last_word = previous.rsplit(" ", 1)[-1].rstrip(".").lower() if previous else ""
        # Too short to stand alone, or the break was an abbreviation's full stop.
        if previous and (len(part.split()) < MIN_LINE_WORDS or last_word in _ABBREVIATIONS
                         or len(previous.split()) < MIN_LINE_WORDS):
            sentences[-1] = f"{previous} {part}"
        else:
            sentences.append(part)
    return sentences


def lines_of(chapter: Chapter) -> list:
    """A chapter as display lines, each knowing where it sits in the chapter.

    Sentences, not paragraphs: a line highlighted while it is read has to be
    short enough to find with your eye, which is the whole trick the lyrics
    panel pulls off.
    """
    cached = getattr(chapter, "_lines", None)
    if cached is not None:
        return cached
    lines: list[Line] = []
    counted = 0
    for position, block in enumerate(chapter.blocks):
        text = _SPACE.sub(" ", _TAGS.sub("", block)).strip()
        if not text:
            continue
        heading = block.startswith("<h3>")
        if lines and not heading:
            lines.append(Line(text="", blank=True, start_word=counted))
        pieces = [text] if heading else _sentences(text)
        for piece in pieces:
            # Counted the way `chapter_words` counts, so that a line's position
            # and a matched passage's position mean the same thing. Splitting on
            # whitespace instead would drift apart over a chapter — "self-aware"
            # is one word to one of them and two to the other.
            words = len(normalise(piece).split())
            lines.append(Line(text=piece, words=words, start_word=counted,
                              heading=heading))
            counted += words
    chapter._lines = lines
    return lines


def chapter_words(chapter: Chapter) -> list:
    """Every word of a chapter, normalised, with the line each one came from."""
    cached = getattr(chapter, "_word_index", None)
    if cached is not None:
        return cached
    words: list[tuple[str, int]] = []
    for index, line in enumerate(lines_of(chapter)):
        if line.blank:
            continue
        for word in normalise(line.text).split():
            words.append((word, index))
    chapter._word_index = words
    return words


# ------------------------------------------------------------- lining it up

def normalise(text: str) -> str:
    """Lowercase words only, so punctuation and spacing stop mattering."""
    return " ".join(_WORD.findall((text or "").lower()))


def _roman(token: str) -> int | None:
    total = previous = 0
    for letter in reversed(token):
        value = _ROMAN_VALUES.get(letter)
        if value is None:
            return None
        total += -value if value < previous else value
        previous = max(previous, value)
    return total or None


def _value(token: str, counted: bool) -> int | None:
    """One word read as a number, if it can be."""
    if token.isdigit():
        return int(token)
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    # Roman numerals are only safe where something is clearly being counted:
    # plenty of ordinary words ("mix", "did") are made of roman letters.
    if counted and _ROMAN.match(token):
        return _roman(token)
    return None


def chapter_number(title: str) -> int | None:
    """The number a chapter title announces, however it is written.

    A number that follows the word *chapter* wins over one that merely starts
    the title: audiobook chapters are often named "06 Chapter 4 - The Shattered
    Plains", where the 06 is the track and the 4 is the chapter.
    """
    words = re.findall(r"[\w']+", (title or "").lower())
    if not words:
        return None
    for position, word in enumerate(words[:-1]):
        if word in _COUNTER_WORDS:
            counted = _value(words[position + 1], counted=True)
            if counted is not None:
                return counted
    leading = _value(words[0], counted=len(words) == 1)
    if leading is not None:
        return leading
    for word in words:
        if word.isdigit():
            return int(word)
    return None


def _agreement(audio_title: str, chapter: Chapter) -> float:
    """How much one audio chapter and one text chapter look like each other."""
    audio_key, text_key = normalise(audio_title), normalise(chapter.title)
    score = 0.0
    if audio_key and audio_key == text_key:
        score += 3
    elif len(audio_key) > 4 and len(text_key) > 4 and (
            audio_key in text_key or text_key in audio_key):
        score += 2
    audio_number, text_number = chapter_number(audio_title), chapter_number(chapter.title)
    if audio_number is not None and text_number is not None:
        score += 2 if audio_number == text_number else -1.5
    if chapter.front_matter:
        score -= 0.5              # narrators do not read the copyright page
    return score


def best_shift(audio_titles: list, chapters: list) -> int:
    """The offset that lines the two chapter lists up best.

    Positive means the text runs ahead: text chapter ``i + shift`` belongs with
    audio chapter ``i``, which is the usual case once front matter is counted.
    """
    if not audio_titles or not chapters:
        return 0
    natural = 0
    for index, chapter in enumerate(chapters):
        if not chapter.front_matter:
            natural = index
            break
    best, best_score = natural, 0.0
    for shift in range(-3, len(chapters)):
        score = 0.0
        for index, title in enumerate(audio_titles):
            position = index + shift
            if 0 <= position < len(chapters):
                score += _agreement(title, chapters[position])
        if score > best_score or (score == best_score and
                                  abs(shift - natural) < abs(best - natural)):
            best, best_score = shift, score
    return best if best_score > 0 else natural


def text_index(audio_index: int, shift: int, count: int) -> int:
    """Which text chapter goes with an audio chapter, kept inside the book."""
    if count <= 0:
        return 0
    return max(0, min(audio_index + shift, count - 1))


# ------------------------------------------------------- finding a spoken spot

# Words too common to say anything about which passage this is.
_EVERYWHERE = frozenset("""
the a an and or but if of to in on at by for with from as is was are were be been
being it its he she they them him her his hers their we you i me my your our not
no yes that this these those there here then than so do does did done have has
had will would could should can may might must what when where who whom which how
said say says out up down over under into about after before all any some more
""".split())

MIN_TRANSCRIPT_WORDS = 10
CONFIDENT = 0.16


def _trigrams(words: list) -> list:
    return [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]


def _chapter_vocabulary(chapter: Chapter) -> set:
    cached = getattr(chapter, "_vocabulary", None)
    if cached is None:
        cached = {word for word, _line in chapter_words(chapter)
                  if len(word) > 3 and word not in _EVERYWHERE}
        chapter._vocabulary = cached
    return cached


def _chapter_trigrams(chapter: Chapter) -> dict:
    """Where each three-word run starts in a chapter."""
    cached = getattr(chapter, "_trigrams", None)
    if cached is None:
        words = [word for word, _line in chapter_words(chapter)]
        cached = {}
        for index, trigram in enumerate(_trigrams(words)):
            cached.setdefault(trigram, []).append(index)
        chapter._trigrams = cached
    return cached


def _best_window(chapter: Chapter, wanted: set, span: int) -> tuple[int, float]:
    """The tightest cluster of matching runs in one chapter: (word, share)."""
    index = _chapter_trigrams(chapter)
    hits = sorted(position for trigram in wanted for position in index.get(trigram, ()))
    if not hits:
        return 0, 0.0
    best_start, best_count = hits[0], 0
    right = 0
    for left, start in enumerate(hits):
        while right < len(hits) and hits[right] <= start + span:
            right += 1
        if right - left > best_count:
            best_start, best_count = start, right - left
    return best_start, best_count / max(1, len(wanted))


def locate(transcript: str, chapters: list, near: int = -1) -> tuple:
    """Find a spoken passage in the book: (chapter, word, confidence).

    The transcript comes from a couple of minutes of narration, so it is short,
    misspelt in the way speech recognition always is, and missing the odd word.
    Matching three-word runs rides over all of that: the wrong ones scatter
    across the book, and the right ones pile up in one place.
    """
    words = normalise(transcript).split()
    if len(words) < MIN_TRANSCRIPT_WORDS or not chapters:
        return -1, -1, 0.0
    wanted = set(_trigrams(words))
    if not wanted:
        return -1, -1, 0.0
    distinctive = {word for word in words if len(word) > 3 and word not in _EVERYWHERE}

    # Score every chapter cheaply first; only a handful are worth a close look.
    ranked = []
    for index, chapter in enumerate(chapters):
        if not chapter.words:
            continue
        shared = len(distinctive & _chapter_vocabulary(chapter))
        if near >= 0 and abs(index - near) <= 2:
            shared += 2          # a nudge towards where we think we already are
        if shared:
            ranked.append((shared, index))
    ranked.sort(reverse=True)

    best = (-1, -1, 0.0)
    for _shared, index in ranked[:6]:
        chapter = chapters[index]
        start, share = _best_window(chapter, wanted, len(words))
        if share > best[2]:
            best = (index, start, share)
    return best


def locate_near(chapter: Chapter, transcript: str, around: int,
                span: int = 500) -> tuple:
    """Place a short passage that is known to be near `around`: (word, share).

    Used to time each segment of a transcript once the passage as a whole has
    been found. A handful of words could match anywhere in a chapter, so the
    search is kept to the neighbourhood where it must be.
    """
    words = normalise(transcript).split()
    if len(words) < 4:
        return -1, 0.0
    wanted = set(_trigrams(words))
    if not wanted:
        return -1, 0.0
    index = _chapter_trigrams(chapter)
    low, high = around - span, around + span
    hits = sorted(position for trigram in wanted
                  for position in index.get(trigram, ()) if low <= position <= high)
    if not hits:
        return -1, 0.0
    best_start, best_count, right = hits[0], 0, 0
    reach = max(8, len(words))
    for left, start in enumerate(hits):
        while right < len(hits) and hits[right] <= start + reach:
            right += 1
        if right - left > best_count:
            best_start, best_count = start, right - left
    return best_start, best_count / max(1, len(wanted))


def timeline(chapter: Chapter, segments: list, begin: float, start_word: int) -> list:
    """Pair each segment of a transcript with the word it starts on.

    The result is a short stretch of real timings — [(word, second), ...] — for
    the passage just heard. Because the clip is read from the file rather than
    off the speaker, most of it lies ahead of the playhead, so these timings say
    when the next lines will be spoken rather than only when this one was.
    """
    points: list[tuple[int, float]] = []
    expected = start_word
    spoken_so_far = 0
    for text, offset in segments:
        words = normalise(text).split()
        guess = start_word + spoken_so_far
        spoken_so_far += len(words)
        word, share = locate_near(chapter, text, guess)
        if word < 0 or share < 0.25:
            continue
        if points and word <= points[-1][0]:
            continue                       # the text only ever moves forwards
        if points and begin + offset <= points[-1][1]:
            continue
        points.append((word, begin + offset))
        expected = word
    return points


def line_at_word(chapter: Chapter, word: int) -> int:
    """Which display line a word of a chapter belongs to."""
    stream = chapter_words(chapter)
    if not stream:
        return 0
    return stream[max(0, min(word, len(stream) - 1))][1]


def line_start_word(chapter: Chapter, line: int) -> int:
    lines = lines_of(chapter)
    return lines[line].start_word if 0 <= line < len(lines) else 0


# ---------------------------------------------------------------- finding it

def _tokens(text: str) -> set:
    """The words that say something about which book this is."""
    found = set()
    for word in normalise(text).split():
        if word.isdigit():
            word = str(int(word))        # "01" and "1" are the same volume
        elif word in _NOISE or len(word) < 2:
            continue
        found.add(word)
    return found


def _numbers(text: str) -> set:
    return {int(n) for n in re.findall(r"(?<!\d)\d{1,3}(?!\d)", text or "")}


def _identity(book: dict) -> tuple[set, set]:
    """What names this book, and which volume numbers it claims.

    Tags on an audiobook are often wrong — plenty of m4b files carry the first
    chapter's name as the title — so the folder and file names count too. They
    are usually the most honest thing about a download.
    """
    source = Path(book.get("path") or "")
    folder = source.name if source.is_dir() else source.parent.name
    stem = "" if source.is_dir() else source.stem
    written = f"{book.get('title', '')} {book.get('series', '')} {folder} {stem}"
    return _tokens(written), _numbers(written)


def match_score(book: dict, candidate: Path) -> float:
    """How likely this EPUB holds the text of this audiobook, from 0 to 1.

    A volume number that disagrees is decisive: *Mistborn #5* is not the text
    for *Mistborn #6*, however much of the title the two share.
    """
    wanted, book_numbers = _identity(book)
    found = _tokens(candidate.stem)
    if not wanted or not found:
        return 0.0
    file_numbers = _numbers(candidate.stem)
    if book_numbers and file_numbers and not (book_numbers & file_numbers):
        return 0.0
    shared = wanted & found
    # One word in common is a coincidence — an author's name, or a series every
    # book in the folder belongs to.
    if len(shared) < 2 and wanted != shared:
        return 0.0
    score = len(shared) / min(len(wanted), len(found))
    if book_numbers & file_numbers:
        score += 0.1
    if _tokens(book.get("author") or "") & found and score >= 0.45:
        score += 0.1
    return min(1.0, score)


def beside(book_path: str) -> Path | None:
    """An EPUB sitting in the same folder as the audiobook."""
    source = Path(book_path)
    folder = source if source.is_dir() else source.parent
    try:
        candidates = sorted(p for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in SUFFIXES)
    except OSError:
        return None
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    stem = normalise(source.stem)
    for candidate in candidates:
        if normalise(candidate.stem) == stem:
            return candidate
    return candidates[0]


def search(book: dict, extra_roots: list | None = None, limit: int = 4000) -> Path | None:
    """Hunt for the book's text nearby and return the best match, if any.

    Audio and text are usually filed separately — ``Books/AudioBooks/…`` next to
    ``Books/…`` — so the search starts at the book's own folder and works
    outwards through its library root and that root's parent.
    """
    found = beside(book.get("path", ""))
    if found is not None:
        return found

    source = Path(book.get("path") or "")
    folder = source if source.is_dir() else source.parent
    roots: list[Path] = []
    for candidate in [folder.parent, Path(book.get("root") or ""),
                      Path(book.get("root") or "").parent, *(extra_roots or [])]:
        candidate = Path(candidate)
        if str(candidate) in ("", ".", "/") or not candidate.is_dir():
            continue
        if candidate not in roots and candidate != Path.home():
            roots.append(candidate)

    best, best_score, seen = None, 0.45, 0
    for root in roots:
        for path in _walk(root, limit - seen):
            seen += 1
            score = match_score(book, path)
            if score > best_score:
                best, best_score = path, score
        if best is not None:
            break                       # nearest folder that had an answer wins
    return best


def _walk(root: Path, budget: int):
    """EPUBs under a folder, without wandering into a whole filesystem."""
    import os
    if budget <= 0:
        return
    depth_of_root = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if len(Path(dirpath).parts) - depth_of_root >= 4:
            dirnames[:] = []
        for filename in filenames:
            if filename.lower().endswith(SUFFIXES):
                budget -= 1
                if budget <= 0:
                    return
                yield Path(dirpath) / filename
