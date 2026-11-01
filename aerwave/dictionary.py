"""Looking a word up while you read.

Wiktionary's REST endpoint answers with the English senses of a word, which is
as close to a British dictionary as a free source gets: it carries Commonwealth
spellings as entries in their own right and labels the dialect of a sense.

The catch is that those entries are often only a pointer — *colour* is
"Commonwealth and Ireland standard spelling of color", *realised* is "simple
past and past participle of realise". A reader who asked what a word means is
not helped by being told it is spelled another way, so Aerwave follows the
pointer once and shows what it finds at the other end.

Nothing is sent anywhere until you ask for a word, and answers are kept on disk
so the same word is never looked up twice.
"""

import html
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .paths import CACHE_DIR

REST = "https://en.wiktionary.org/api/rest_v1/page/definition/"
AGENT = "Aerwave/3.0 (desktop media player; dictionary lookups)"
TIMEOUT = 8
MAX_SENSES = 6
MAX_HOPS = 3                           # realised -> realise -> realize
# What a reader wants first. A word like "her" is a pronoun long before it is
# an ISO language code, and Wiktionary does not sort it that way.
PARTS_IN_ORDER = ("noun", "verb", "adjective", "adverb", "pronoun", "determiner",
                  "preposition", "conjunction", "interjection", "numeral",
                  "proper noun", "prefix", "suffix", "particle", "article",
                  "letter", "symbol", "abbreviation")
CACHE_PATH = CACHE_DIR / "dictionary.json"
CACHE_LIMIT = 800
# Bumped whenever the parsing changes, so old answers are re-fetched rather
# than served from a cache that was written by different rules.
CACHE_VERSION = 2

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
# "Commonwealth spelling of color", "plural of parapet", "past participle of realise"
_POINTER = re.compile(
    r"^(?:.*\b(?:spelling|form|forms|plural|singular|participle|tense|comparative|"
    r"superlative|abbreviation|initialism|contraction|alternative\s+\w+)\b.*?)\s+of\s+"
    r"([\w'\-]+)\.?$", re.IGNORECASE)
_WORD_EDGE = re.compile(r"^[^\w]+|[^\w]+$")

_memory: dict[str, "Entry"] = {}
# Two words can be looked up at once, and both would write the cache file.
_writing = threading.Lock()


@dataclass
class Sense:
    part: str = ""
    text: str = ""
    example: str = ""


@dataclass
class Entry:
    word: str = ""
    senses: list = field(default_factory=list)
    note: str = ""                     # how we got here, when a pointer was followed
    error: str = ""

    def __bool__(self) -> bool:
        return bool(self.senses)


def tidy(word: str) -> str:
    """A word as the dictionary spells it: no quotes, no trailing comma, no 's."""
    word = _WORD_EDGE.sub("", (word or "").replace("’", "'").strip())
    if word.lower().endswith("'s"):
        word = word[:-2]
    return word


def _clean(markup: str) -> str:
    # Twice: some entries arrive with their entities escaped a second time.
    text = html.unescape(html.unescape(_TAGS.sub("", markup or "")))
    text = _SPACE.sub(" ", text.replace("\u00a0", " ")).strip()
    return text.lstrip(": ").strip()   # a stripped label often leaves its colon


def _fetch(word: str) -> dict | None:
    url = REST + urllib.parse.quote(word.replace(" ", "_"), safe="")
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except Exception:                  # noqa: BLE001  (404s, offline, rate limits)
        return None


def _rank(part: str, prefer: str = "") -> int:
    part = (part or "").strip().lower()
    if prefer and part == prefer.strip().lower():
        return -1                      # what the pointer said it would be
    try:
        return PARTS_IN_ORDER.index(part)
    except ValueError:
        return len(PARTS_IN_ORDER)


def _senses_from(data: dict, prefer: str = "") -> list:
    blocks = []
    for position, block in enumerate(data.get("en") or []):
        part = (block.get("partOfSpeech") or "").strip()
        blocks.append((_rank(part, prefer), position, part, block))
    senses: list[Sense] = []
    said: set[str] = set()
    for _rank_of, _position, part, block in sorted(blocks):
        for definition in block.get("definitions") or []:
            text = _clean(definition.get("definition"))
            if not text:
                continue
            example = ""
            for raw in (definition.get("parsedExamples") or definition.get("examples") or []):
                candidate = _clean(raw.get("example") if isinstance(raw, dict) else raw)
                if len(candidate.split()) >= 3:      # "gunmetal:" on its own is no example
                    example = candidate
                    break
            spoken = text.lower().rstrip(".")
            if spoken in said:
                continue               # the same sense twice, once with a label
            said.add(spoken)
            senses.append(Sense(part=part, text=text, example=example))
            if len(senses) >= MAX_SENSES:
                return senses
    return senses


def _pointer_target(senses: list) -> str:
    """The word a 'spelling of …' entry is really pointing at."""
    if not senses:
        return ""
    match = _POINTER.match(senses[0].text)
    return match.group(1) if match else ""


def look_up(word: str) -> Entry:
    """What a word means. Never raises — it reports instead."""
    word = tidy(word)
    if not word:
        return Entry(error="no word there")
    key = word.lower()
    if key in _memory:
        return _memory[key]
    cached = _from_disk(key)
    if cached is not None:
        _memory[key] = cached
        return cached

    data = _fetch(word) or (_fetch(word.lower()) if word != word.lower() else None)
    if data is None:
        entry = Entry(word=word, error=f"no dictionary entry for “{word}”")
        _memory[key] = entry
        return entry                   # not cached to disk: it may be a network blip

    entry = Entry(word=word, senses=_senses_from(data))
    # "Commonwealth spelling of colour" is true but useless on its own, and the
    # trail can run two deep: realised -> realise -> realize.
    seen = {key}
    trail = [word]
    for _hop in range(MAX_HOPS):
        target = _pointer_target(entry.senses)
        if not target or target.lower() in seen:
            break
        seen.add(target.lower())
        # "simple past of stumble" is a verb, whatever else stumble can be.
        expected = entry.senses[0].part if entry.senses else ""
        followed = _fetch(target)
        deeper = _senses_from(followed, prefer=expected) if followed else []
        if not deeper:
            break
        trail.append(target)
        entry = Entry(word=word, senses=deeper)
    if len(trail) > 1:
        entry.note = " → ".join(trail)
    if not entry.senses:
        entry.error = f"no English entry for “{word}”"
    _memory[key] = entry
    _to_disk(key, entry)
    return entry


def _from_disk(key: str) -> Entry | None:
    store = _store()
    found = store.get(key)
    if not isinstance(found, dict) or found.get("v") != CACHE_VERSION:
        return None
    return Entry(word=found.get("word", key),
                 senses=[Sense(**s) for s in found.get("senses", [])],
                 note=found.get("note", ""))


def _to_disk(key: str, entry: Entry) -> None:
    if not entry.senses:
        return
    with _writing:
        store = _store()
        store[key] = {"v": CACHE_VERSION, "word": entry.word, "note": entry.note,
                      "senses": [vars(sense) for sense in entry.senses]}
        if len(store) > CACHE_LIMIT:
            for stale in list(store)[:len(store) - CACHE_LIMIT]:
                store.pop(stale, None)
        # Written beside and moved into place: a half-written cache would be
        # read back as no cache at all, losing every word looked up so far.
        part = CACHE_PATH.with_suffix(".part")
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            part.write_text(json.dumps(store), encoding="utf-8")
            os.replace(part, CACHE_PATH)
        except OSError:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass


def _store() -> dict:
    global _disk
    if _disk is None:
        try:
            _disk = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _disk = {}
    return _disk


_disk: dict | None = None
