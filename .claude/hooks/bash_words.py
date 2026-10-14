"""Reading a Bash command the way bash would, for the hooks in this folder.

A command line becomes its simple commands, each a list of words with quotes resolved, plus the files its
output is redirected to. One pass reads it as bash does: quotes, comments, line continuations, heredocs
(their bodies are data, not commands) and command substitutions ($(...), `...`, <(...), >(...)), whose
commands are read too, at any depth. Runs on the system python3 (3.9 on macOS), so no newer syntax.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

# Words that run the word after them as the command, and words that end a compound command.
PREFIXES = {"time", "then", "do", "else", "elif", "if", "while", "until", "nohup", "exec", "builtin", "env",
            "sudo", "!", "{", "}", "fi", "done", "esac"}
# Commands whose other words are data: `for f in $(...)`, `case $x in`.
DATA_ONLY = {"for", "case", "select"}
# Redirect targets that aren't files.
STREAMS = {"/dev/null", "/dev/stdout", "/dev/stderr"}
# Where a substitution was: its commands are read on their own.
SUBSTITUTED = ""

# Characters inside quotes are hidden behind these while shlex splits words, so a quoted '>' or '|' stays text.
_HIDDEN = {c: chr(0xE000 + n) for n, c in enumerate(";|&<>()$`\n#")}
_HIDE = str.maketrans(_HIDDEN)
_SHOW = str.maketrans({v: k for k, v in _HIDDEN.items()})
_WORD_BREAKS = set(" \t\n;&|()<>")
_DELIMITER = re.compile(r"""(-?)[ \t]*(?:'([^']*)'|"([^"]*)"|([^\s<>|&;()'"]+))""")


@dataclass
class Simple:
    words: list[str]
    writes: list[str] = field(default_factory=list)  # files its output is redirected to

    @property
    def name(self) -> str:
        return os.path.basename(self.words[0]) if self.words else ""


@dataclass
class Parsed:
    commands: list[Simple]
    heredocs: list[str]  # every heredoc body, at any depth


def parse(command: str) -> Parsed:
    """Every simple command in a command line. Raises ValueError when it can't be read (an open quote)."""
    scanner = _Scanner(command)
    top = scanner.read()
    commands: list[Simple] = []
    for text in [*scanner.inner, top]:
        commands += _split(text)
    for simple in commands:
        simple.words = _command_words(simple.words)
    return Parsed([c for c in commands if c.words or c.writes], scanner.bodies)


def simple_commands(command: str) -> list[Simple]:
    return parse(command).commands


def _command_words(words: list[str]) -> list[str]:
    """The command itself: without `time`, `then` and the like or NAME=value settings before it; nothing
    for `for`/`case`, whose words are data."""
    i = 0
    while i < len(words) and (words[i] in PREFIXES or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i])):
        i += 1
    if i < len(words) and words[i] == "command" and not words[i + 1:i + 2] == ["-v"] and not words[i + 1:i + 2] == ["-V"]:
        i += 1  # `command git stash` runs git; `command -v git` only looks it up
    if i < len(words) and words[i] in DATA_ONLY:
        return []
    return words[i:]


def _split(text: str) -> list[Simple]:
    """Scanned text (quoted operators hidden, no comments or heredoc bodies) as simple commands."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    found: list[Simple] = []
    current = Simple([])
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token and set(token) <= set("();|&"):
            found.append(current)
            current = Simple([])
            i += 1
        elif token and set(token) <= set("();<>|&"):
            target = tokens[i + 1].translate(_SHOW) if i + 1 < len(tokens) else ""
            stream = target in STREAMS or (token.endswith("&") and (target.isdigit() or target == "-"))
            if ">" in token and not token.startswith("<") and not stream:
                current.writes.append(target)
            i += 2
        elif token.isdigit() and i + 1 < len(tokens) and tokens[i + 1][:1] in ("<", ">"):
            i += 1  # a file descriptor: the 2 in 2>/dev/null
        else:
            current.words.append(token.translate(_SHOW))
            i += 1
    found.append(current)
    return found


class _Scanner:
    """One pass over a command line, keeping what shlex needs and collecting substitutions and heredocs."""

    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.inner: list[str] = []   # the scanned text of every substitution, at any depth
        self.bodies: list[str] = []  # every heredoc body

    def read(self, closing: bool = False) -> str:
        """From here: quoted operators hidden, comments and heredoc bodies taken out, substitutions replaced
        by SUBSTITUTED. With closing, stops after the ) that ends the $( or <( being read."""
        t = self.text
        out: list[str] = []
        heredocs: list[tuple[str, bool, bool]] = []  # delimiter, quoted, tabs stripped: read at the next newline
        depth = 0
        while self.i < len(t):
            c, rest = t[self.i], t[self.i:self.i + 3]
            if c == "\\":
                if rest[:2] != "\\\n":
                    out.append(t[self.i:self.i + 2].translate(_HIDE))
                self.i += 2
            elif c == "'":
                end = t.find("'", self.i + 1)
                if end < 0:
                    raise ValueError("an open quote")
                out.append("'" + t[self.i + 1:end].translate(_HIDE) + "'")
                self.i = end + 1
            elif c == '"':
                self.i += 1
                out.append('"' + self._double() + '"')
            elif c == "#" and (not out or out[-1][-1:] in _WORD_BREAK_STRINGS):
                end = t.find("\n", self.i)
                self.i = len(t) if end < 0 else end
            elif rest == "$((":
                self.i += 3
                self._skip_arithmetic()
                out.append("0")
            elif rest[:2] in ("$(", "<(", ">("):
                self.i += 2
                self.inner.append(self.read(closing=True))
                out.append(SUBSTITUTED if c == "$" else f" {SUBSTITUTED} ")
            elif c == "`":
                self._backticks()
                out.append(SUBSTITUTED)
            elif rest == "<<<":
                out.append("<<<")
                self.i += 3
            elif rest[:2] == "<<":
                match = _DELIMITER.match(t, self.i + 2)
                if not match:
                    out.append("<<")
                    self.i += 2
                    continue
                word = next(g for g in match.groups()[1:] if g is not None)
                heredocs.append((word, match.group(4) is None, match.group(1) == "-"))
                out.append(" << H ")
                self.i = match.end()
            elif c == "\n":
                out.append(" ; ")
                self.i += 1
                for word, quoted, tabs in heredocs:
                    body = self._heredoc_body(word, tabs)
                    self.bodies.append(body)
                    if not quoted:  # bash expands $(...) in it
                        self._expand(body)
                heredocs = []
            elif c == "(":
                depth += 1
                out.append(c)
                self.i += 1
            elif c == ")":
                self.i += 1
                if depth:
                    depth -= 1
                elif closing:
                    return "".join(out)
                out.append(c)
            else:
                out.append(c)
                self.i += 1
        if closing:
            raise ValueError("an open $(")
        return "".join(out)

    def _double(self, until_quote: bool = True) -> str:
        """Inside double quotes (or an unquoted heredoc body): everything is text but substitutions."""
        t = self.text
        out: list[str] = []
        while self.i < len(t):
            c, rest = t[self.i], t[self.i:self.i + 3]
            if c == "\\":
                out.append(t[self.i:self.i + 2].translate(_HIDE))
                self.i += 2
            elif c == '"' and until_quote:
                self.i += 1
                return "".join(out)
            elif rest == "$((":
                self.i += 3
                self._skip_arithmetic()
                out.append("0")
            elif rest[:2] == "$(":
                self.i += 2
                self.inner.append(self.read(closing=True))
                out.append(SUBSTITUTED)
            elif c == "`":
                self._backticks()
                out.append(SUBSTITUTED)
            else:
                out.append(c.translate(_HIDE))
                self.i += 1
        if until_quote:
            raise ValueError("an open quote")
        return "".join(out)

    def _backticks(self) -> None:
        t, j, inner = self.text, self.i + 1, []
        while j < len(t) and t[j] != "`":
            if t[j] == "\\" and j + 1 < len(t):
                j += 1
            inner.append(t[j])
            j += 1
        if j >= len(t):
            raise ValueError("an open backtick")
        self.i = j + 1
        self._absorb(_Scanner("".join(inner)), until_quote=None)

    def _expand(self, body: str) -> None:
        self._absorb(_Scanner(body), until_quote=False)

    def _absorb(self, other: _Scanner, until_quote: bool | None) -> None:
        """Reads another text with its own scanner (a backtick command, or a heredoc body as if in double
        quotes) and keeps what it found."""
        if until_quote is None:
            self.inner.append(other.read())
        else:
            other._double(until_quote=False)
        self.inner += other.inner
        self.bodies += other.bodies

    def _heredoc_body(self, word: str, tabs: bool) -> str:
        t, lines = self.text, []
        while self.i < len(t):
            end = t.find("\n", self.i)
            line = t[self.i:] if end < 0 else t[self.i:end]
            self.i = len(t) if end < 0 else end + 1
            if (line.lstrip("\t") if tabs else line) == word:
                break
            lines.append(line)
        return "\n".join(lines)

    def _skip_arithmetic(self) -> None:
        depth = 2
        while self.i < len(self.text) and depth:
            depth += {"(": 1, ")": -1}.get(self.text[self.i], 0)
            self.i += 1


_WORD_BREAK_STRINGS = _WORD_BREAKS | {""}
