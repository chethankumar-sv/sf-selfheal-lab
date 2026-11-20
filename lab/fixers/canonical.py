"""missing_canonical / wrong_canonical -> a self-referencing canonical link."""
from __future__ import annotations

from .base import CANONICAL_RE, FixError, Fixer, FixResult, esc, insert_in_head


class CanonicalFixer(Fixer):
    name = "canonical"
    issue_ids = ("missing_canonical", "wrong_canonical")

    def apply(self, ctx, findings):
        result = FixResult()
        for f in findings:
            path = ctx.path_of(f.url)
            tag = f'<link rel="canonical" href="{{{{BASE}}}}{esc(path)}">'

            def transform(text, tag=tag):
                if CANONICAL_RE.search(text):
                    return CANONICAL_RE.sub(tag, text, count=1)
                return insert_in_head(text, tag)

            try:
                file = ctx.edit(f.url, transform, result)
                result.notes.append(f"{f.issue_id}: {ctx.rel(file)} -> canonical set to {path}")
            except FixError as e:
                result.skipped.append(f"{f.issue_id} {path}: {e}")
        return result

    def suggest(self, finding):
        return (f"In the <head> of {finding.url} use exactly one canonical pointing at itself:\n"
                f'    <link rel="canonical" href="{finding.url}">\n'
                f"Remove any other canonical tag on the page.")
