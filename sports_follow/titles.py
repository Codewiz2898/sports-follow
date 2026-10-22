"""A video's title as the Feed shows it: as YouTube gives it (BHA-652). YouTube's API policies want a video's title
shown to the viewer unmodified, so its emoji, hashtags, channel's name and capitals stay as its maker wrote them
(until then the Feed took them off and put a shouted title in sentence case, BHA-349). Only HTML's escapes are
read, however many times over a title came escaped ("Mbappe &amp; Bijan", as a search's titles came before BHA-358
read them as written): they're how the text was sent, not the title. Read as it's served, never as it's stored.
"""

from __future__ import annotations

import html


def shown(title: str) -> str:
    """The title as the Feed shows it: HTML's escapes read, however many times over (three at most), and the spaces
    around it off."""
    text = (title or "").strip()
    for _ in range(3):
        read = html.unescape(text)
        if read == text:
            break
        text = read
    return text
