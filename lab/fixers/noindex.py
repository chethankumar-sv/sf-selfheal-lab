"""noindex -> remove the directive from the robots meta tag. Needs explicit human approval."""
from __future__ import annotations

import re

from .base import FixError, Fixer, FixResult

ROBOTS_META_RE = re.compile(r'[ \t]*<meta\b[^>]*\bname=["\']robots["\'][^>]*>[ \t]*\n?', re.I)
CONTENT_RE = re.compile(r'(\bcontent=["\'])([^"\']*)(["\'])', re.I)


def _strip_noindex(text):
    def repl(match):
        tag = match.group(0)
        content = CONTENT_RE.search(tag)
        if not content or "noindex" not in content.group(2).lower():
            return tag
        kept = [t.strip() for t in content.group(2).split(",") if t.strip() and t.strip().lower() != "noindex"]
        kept = [t for t in kept if t.lower() not in ("follow", "index")]  # these are the defaults anyway
        if not kept:
            return ""  # nothing meaningful left: drop the whole tag
        return CONTENT_RE.sub(lambda m: f"{m.group(1)}{', '.join(kept)}{m.group(3)}", tag, count=1)

    new = ROBOTS_META_RE.sub(repl, text)
    if new == text:
        raise FixError("no noindex in a robots <meta> tag (an X-Robots-Tag header needs a server config change)")
    return new


class NoindexFixer(Fixer):
    name = "noindex"
    issue_ids = ("noindex",)
    approval = "noindex"  # noindex is often intentional, so a human must opt in

    def apply(self, ctx, findings):
        result = FixResult()
        for f in findings:
            path = ctx.path_of(f.url)
            try:
                file = ctx.edit(f.url, _strip_noindex, result)
                result.notes.append(f"noindex: {ctx.rel(file)} -> noindex removed")
            except FixError as e:
                result.skipped.append(f"noindex {path}: {e}")
        return result

    def suggest(self, finding):
        return (f"Confirm {finding.url} is meant to be hidden from search. If not, remove 'noindex' from\n"
                '    <meta name="robots" content="...">  (and from any X-Robots-Tag response header).')
