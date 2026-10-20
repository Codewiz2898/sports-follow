"""How names are compared: athletes, aliases and teams, across every source and the registry.

Names are compared as plain lower-case ASCII words, so "Jokić", "jokic" and "JOKIC" are one name, and
"A'ja Wilson" is "A’ja Wilson".
"""

from __future__ import annotations

import re
import unicodedata

# Letters Unicode doesn't decompose into a base letter plus an accent. Ð (U+00D0, the Icelandic
# capital eth) is often typed for Đ.
_LETTERS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss", "đ": "d", "Đ": "D", "Ð": "D", "ł": "l", "Ł": "L", "þ": "th", "Þ": "Th", "ð": "d", "ı": "i"})
# Letters with a second plain spelling: Đ is "dj" in English (Wikidata writes Novak Djokovic), and ü,
# ö, ä are "ue", "oe", "ae" in German (Thomas Müller is Thomas Mueller).
NATIVE = str.maketrans({"đ": "dj", "Đ": "Dj", "Ð": "Dj", "ü": "ue", "Ü": "Ue", "ö": "oe", "Ö": "Oe", "ä": "ae", "Ä": "Ae"})


def fold(text: str) -> str:
    """Case- and accent-insensitive form for comparing names ("Jokić" == "jokic", "Øen" == "oen")."""
    return unicodedata.normalize("NFKD", text.translate(_LETTERS)).encode("ascii", "ignore").decode().lower().strip()


def words(text: str) -> list[str]:
    """A name as plain lower-case ASCII words, the one form names are compared in: "A'ja Wilson" and
    "A’ja Wilson" -> ["aja", "wilson"], "Jokić" -> ["jokic"], "M.S. Dhoni" -> ["m", "s", "dhoni"]."""
    return re.sub(r"[^a-z0-9]+", " ", fold(re.sub(r"['’`]", "", text or ""))).split()


def spellings(text: str) -> list[list[str]]:
    """A name's words in each of its plain spellings: words(text), then, if it has Đ, ü, ö or ä, with
    those spelled out ("Thomas Müller" -> ["thomas", "muller"] and ["thomas", "mueller"])."""
    plain = words(text)
    native = words(unicodedata.normalize("NFC", text or "").translate(NATIVE))
    return [plain] if native == plain else [plain, native]


# How much of a description says who and what it's about: its first paragraph, up to the first blank line or a
# line with a link in it, and no more than this many characters (cut at a word). What's under it (related videos,
# podcasts, a channel's own links and sponsors) is about someone or something else.
LEAD_MAX = 300
_LINK = re.compile(r"https?://|www\.", re.I)


def lead(description: str) -> str:
    """A description's opening (LEAD_MAX)."""
    kept: list[str] = []
    for line in (description or "").strip().splitlines():
        if not line.strip() or (kept and _LINK.search(line)):
            break
        kept.append(line)
    text = "\n".join(kept)
    if len(text) <= LEAD_MAX:
        return text
    return text[:LEAD_MAX].rstrip() if text[LEAD_MAX].isspace() else text[: LEAD_MAX + 1].rsplit(None, 1)[0]


def team_key(name: str) -> str:
    """A team name reduced for exact comparison: "Australia 'A'" == "australia a"; never a substring match."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(name)).split())


def names_match(wanted: str, found: str) -> bool:
    """Accept a search hit only if the surnames agree and every word of the shorter name is in the
    longer one (an initial stands for its word): "Ronaldo" is Cristiano Ronaldo and "Jasprit Bumrah" is
    "Jasprit Jasbirsingh Bumrah", but "Ahmed Salah" isn't Mohamed Salah."""
    w, f = words(wanted), words(found)
    if not (w and f) or not (w[-1] == f[-1] or w[-1] in f):
        return False
    short, long = (w, f) if len(w) <= len(f) else (f, w)
    return all(any(a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)) for b in long) for a in short)


def same(query: str, name: str) -> bool:
    """The name is exactly what was typed, in any order and either spelling ("thomas mueller" is Thomas Müller)."""
    names = [sorted(n) for n in spellings(name)]
    return any(q and sorted(q) in names for q in spellings(query))


def prefix_match(query: str, name: str) -> bool:
    """Every typed word starts some word of the name, in either spelling: "caitl" -> Caitlin Clark,
    "v kohli" -> Virat Kohli, "Đoković" -> Djokovic. Never when nothing searchable was typed ("--")."""
    names = spellings(name)
    return any(typed and all(any(w.startswith(q) for w in n) for q in typed) for typed in spellings(query) for n in names)
