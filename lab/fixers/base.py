"""Shared types and HTML helpers for the fixers."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class FixError(Exception):
    """A fixer could not safely patch this finding. The message becomes a 'skipped' note."""


@dataclass
class FixResult:
    changed: set = field(default_factory=set)   # files touched, relative to the site dir
    notes: list = field(default_factory=list)   # what was done
    skipped: list = field(default_factory=list) # what needs a human


@dataclass
class Context:
    site_dir: Path
    inlinks: list = field(default_factory=list)  # rows of the Client Error (4xx) Inlinks export
    config: dict = field(default_factory=dict)
    approvals: set = field(default_factory=set)

    @staticmethod
    def path_of(url):
        return urlsplit(url).path or "/"

    def file_for(self, url):
        path = self.path_of(url)
        rel = "index.html" if path == "/" else path.lstrip("/")
        file = (self.site_dir / rel).resolve()
        if self.site_dir.resolve() not in file.parents or not file.is_file():
            raise FixError(f"no source file for {path} (looked for site/{rel})")
        return file

    def rel(self, file):
        return str(Path(file).resolve().relative_to(self.site_dir.resolve()))

    def edit(self, url, transform, result):
        """Read the file behind `url`, apply transform(text) -> text, write it back if it changed."""
        file = self.file_for(url)
        before = file.read_text(encoding="utf-8")
        after = transform(before)
        if after != before:
            file.write_text(after, encoding="utf-8")
            result.changed.add(self.rel(file))
        return file


class Fixer:
    name = ""
    issue_ids: tuple = ()
    approval = None  # set to a key (e.g. "noindex") when a human must opt in before the fixer runs

    def apply(self, ctx, findings) -> FixResult:
        raise NotImplementedError

    def suggest(self, finding) -> str:
        """Copy-paste advice for teams that have no code access."""
        return ""


# ---- small HTML helpers (regex based; fine for the lab's hand-written pages) -----------------

CANONICAL_RE = re.compile(r'<link\b[^>]*\brel=["\']canonical["\'][^>]*>', re.I)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.I | re.S)
H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)


def get_title(text):
    m = TITLE_RE.search(text)
    return html.unescape(m.group(1).strip()) if m else ""


def get_h1(text):
    m = H1_RE.search(text)
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1)).strip()) if m else ""


def insert_in_head(text, snippet):
    m = re.search(r"^([ \t]*)</head>", text, re.I | re.M)
    if not m:
        raise FixError("page has no </head> on its own line")
    return text[:m.start()] + f"{m.group(1)}  {snippet}\n" + text[m.start():]


def insert_at_top_of_body(text, snippet):
    """Insert as the first child of <main> (or <body> if there is no <main>)."""
    m = re.search(r"^([ \t]*)<main\b[^>]*>", text, re.I | re.M) or re.search(r"^([ \t]*)<body\b[^>]*>", text, re.I | re.M)
    if not m:
        raise FixError("page has neither <main> nor <body> at line start")
    eol = text.find("\n", m.end())
    eol = len(text) if eol == -1 else eol + 1
    return text[:eol] + f"{m.group(1)}  {snippet}\n" + text[eol:]


def esc(value):
    return html.escape(value, quote=True)
