"""A video's title as the Feed shows it (BHA-349): tidied as it's served, never as it's stored, so a better rule
reaches every row at once. The words stay; what comes off is what shouts:

- emoji, and the flags, arrows and stars channels put round their words;
- the hashtags at the end ("… #cricket #shorts"), and the # of one in the middle ("#ViratKohli" is a word);
- the channel's own name after a bar or a dash at the end ("… | Sports Yaari"), and a bar with nothing after it;
  the bars left between its parts become the app's own dots ("Man City 5-1 AEK · Champions League");
- runs of punctuation ("..!", "!!!"), a space before a stop, and runs of spaces;
- a part in capitals ("PRESS CONFERENCE: SHREYAS IYER READY") is put in sentence case: its first word and each
  sentence's capitalised, every other word lower case but the ones a fan would write in capitals or with one, the
  names the caller keeps (the players', their sport's competitions and sides: Sport.title_words) as they're written,
  and "I". A part only partly in capitals ("IND vs AUS 2nd ODI") is left as it is: those are names.

`tidy` never returns nothing: a title with nothing left after all that is the title as it came.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Emoji and the pictographs around them: the emoji blocks, dingbats, arrows, stars, keycaps, variation selectors,
# the joiner that builds a family of them, skin tones, and the regional letters flags are made of.
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # mahjong to symbols and pictographs extended-A, flags' regional letters, skin tones
    "←-⇿"  # arrows
    "⌀-⏿"  # technical: ⌚ ⏰ ⏩
    "①-⓿"  # enclosed letters and numbers: ⓘ
    "■-➿"  # shapes, symbols, dingbats: ▶ ☀ ✅ ❤ ➡
    "⤀-⥿"  # supplemental arrows: ⤴
    "⬀-⯿"  # stars and arrows: ⭐ ⬆
    "〰〽㊗㊙"
    "︀-️"  # variation selectors
    "‍⃣"  # the joiner, the keycap
    "\U000e0000-\U000e007f"  # tags (a subdivision's flag)
    "]+"
)
_TRAILING_TAGS = re.compile(r"(?:[\s|,.\-–—]*#[^\W_][\w]*)+[\s|,.\-–—]*$")
_TAG_MARK = re.compile(r"#(?=[^\W\d_])")  # a hashtag in the middle: its word stays
_SEPARATOR = re.compile(r"\s+(?:[|｜]|[-–—](?=\s))\s*")
_BAR = re.compile(r"\s*[|｜]\s*")
_PUNCT_RUN = re.compile(r"[.!?]*([!?])[.!?]*|\.{4,}")
_SPACE_BEFORE = re.compile(r"\s+([,.!?:;])")
_SPACES = re.compile(r"\s+")
_ENDS = re.compile(r"^[\s|｜:\-–—,;]+|[\s|｜:\-–—,;]+$")
_WORD = re.compile(r"[^\W\d_][\w'’.-]*")  # a word with a letter first: "IPL", "T20I", "Kohli's", "Chess.com"
_SENTENCE = re.compile(r"(^|[.!?]\s+|(?<![^\s:])[\"“‘(\[])([^\W\d_])")  # a sentence's first letter, and an opening quote's
SHOUTED = 0.6  # of a title's words of two letters or more, this many in capitals and it's put in sentence case
MIN_WORDS = 3  # a title of fewer words of two letters or more is never taken for shouting ("IND v AUS")


def tidy(title: str, channel: str | None = None, keep: Iterable[str] = ()) -> str:
    """The title as the Feed shows it (see the module's docstring). channel: its channel's name, which comes off its
    end; keep: names written as they are when it's put in sentence case (a player's, their sport's title_words)."""
    raw = (title or "").strip()
    text = _EMOJI.sub(" ", raw)
    text = _TRAILING_TAGS.sub("", text)
    text = _TAG_MARK.sub("", text)
    text = _without_channel(text, channel)
    text = _PUNCT_RUN.sub(lambda m: m.group(1) or "...", text)
    text = _SPACE_BEFORE.sub(r"\1", _SPACES.sub(" ", text))
    text = _ENDS.sub("", text).strip()
    # Each part between the bars that are left is read alone: "FODEN HAT-TRICK! | Man City 5-1 AEK" shouts in its
    # first part only. The bars become the dots the app's own lines use.
    parts = [sentence_case(p, keep) if shouted(p) else p for p in _BAR.split(text)]
    return " · ".join(p for p in parts if p) or raw


def shouted(text: str) -> bool:
    """Whether a title is in capitals: SHOUTED of its words of two letters or more, and MIN_WORDS of them at least."""
    words = [w for w in _WORD.findall(text) if sum(c.isalpha() for c in w) >= 2]
    if len(words) < MIN_WORDS:
        return False
    loud = sum(1 for w in words if w.upper() == w)
    return loud / len(words) >= SHOUTED


def sentence_case(text: str, keep: Iterable[str] = ()) -> str:
    """A shouted title in sentence case: lower case, then each sentence's (and quote's) first letter capitalised,
    then each name kept as it's written ("IPL", "Shreyas Iyer", and each word of a name: "Iyer's"), and "I"."""
    out = text.lower()
    names = {n.strip() for n in keep if n and n.strip()}
    # Each word of a name of two words or more, too ("Kohli" of "Virat Kohli"), when it's a name's own (three letters up).
    names |= {w for n in list(names) if " " in n for w in n.split() if len(w) >= 3 and w[:1].isupper()}
    for name in sorted(names, key=len, reverse=True):
        out = re.sub(rf"(?<![\w]){re.escape(name.lower())}(?![\w])", lambda _m, name=name: name, out)
    out = re.sub(r"(?<![\w'’])i(?=$|[\s'’,.!?:;])", "I", out)
    return _SENTENCE.sub(lambda m: m.group(1) + m.group(2).upper(), out)


def _without_channel(text: str, channel: str | None) -> str:
    """The title without its channel's name after a bar or a dash at its end, nor empty bars at its end."""
    parts = _SEPARATOR.split(text)
    mine = _plain(channel or "")
    while len(parts) > 1:
        last = _plain(parts[-1])
        if not last or (mine and len(last) >= 3 and (last == mine or last in mine or mine in last)):
            parts.pop()
            continue
        break
    if len(parts) == 1:
        return parts[0]
    # The bars between what's left stay as they were written.
    kept = _SEPARATOR.findall(text)[: len(parts) - 1]
    return "".join(p + sep for p, sep in zip(parts, [*kept, ""]))


def _plain(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).lower()
