"""duplicate_title -> make titles unique, using each page's own H1 (or slug) as the distinguishing part."""
from __future__ import annotations

import re
from collections import defaultdict

from .base import TITLE_RE, FixError, Fixer, FixResult, esc, get_h1, get_title


def _suffix(title):
    parts = re.split(r"\s+[|\u2013\u2014-]\s+", title, maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _slug_title(path):
    return path.strip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()


class DuplicateTitleFixer(Fixer):
    name = "titles"
    issue_ids = ("duplicate_title",)

    def apply(self, ctx, findings):
        result = FixResult()
        used = {get_title(f.read_text(encoding="utf-8")) for f in ctx.site_dir.rglob("*.html")}

        groups = defaultdict(list)
        for f in findings:
            try:
                file = ctx.file_for(f.url)
            except FixError as e:
                result.skipped.append(f"duplicate_title {ctx.path_of(f.url)}: {e}")
                continue
            groups[get_title(file.read_text(encoding="utf-8"))].append(f)

        for title, group in groups.items():
            # keep the first page's title, rewrite the others
            for f in sorted(group, key=lambda x: ctx.path_of(x.url))[1:]:
                path = ctx.path_of(f.url)
                holder = {}

                def transform(text, path=path, holder=holder):
                    base = get_h1(text) or _slug_title(path)
                    suffix = _suffix(get_title(text))
                    new = f"{base} | {suffix}" if suffix else base
                    if new in used:
                        new = f"{base} ({_slug_title(path)}) | {suffix}" if suffix else f"{base} ({_slug_title(path)})"
                    used.add(new)
                    holder["title"] = new
                    return TITLE_RE.sub(lambda m: f"<title>{esc(new)}</title>", text, count=1)

                file = ctx.edit(f.url, transform, result)
                result.notes.append(f'duplicate_title: {ctx.rel(file)} -> "{holder["title"]}"')
        return result

    def suggest(self, finding):
        return (f"{finding.url} shares its title with other pages ({finding.detail}). "
                "Write a unique title for each page that names that page's own topic.")
