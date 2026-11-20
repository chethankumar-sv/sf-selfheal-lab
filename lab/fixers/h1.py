"""missing_h1 -> add an H1 derived from the title. multiple_h1 -> keep the first, demote the rest to H2."""
from __future__ import annotations

import re

from .base import H1_RE, FixError, Fixer, FixResult, esc, get_title, insert_at_top_of_body


def _heading_from_title(title):
    """'Home Loan Eligibility Guide | Lab' -> 'Home Loan Eligibility Guide'."""
    return re.split(r"\s+[|\u2013\u2014-]\s+", title)[0].strip()


class H1Fixer(Fixer):
    name = "h1"
    issue_ids = ("missing_h1", "multiple_h1")

    def apply(self, ctx, findings):
        result = FixResult()
        for f in findings:
            path = ctx.path_of(f.url)
            try:
                if f.issue_id == "missing_h1":
                    def add_h1(text):
                        if H1_RE.search(text):
                            return text
                        heading = _heading_from_title(get_title(text))
                        if not heading:
                            raise FixError("page has no title to derive an H1 from")
                        return insert_at_top_of_body(text, f"<h1>{esc(heading)}</h1>")
                    file = ctx.edit(f.url, add_h1, result)
                    result.notes.append(f"missing_h1: {ctx.rel(file)} -> H1 added from the title")
                else:
                    def demote_extra_h1s(text):
                        seen = {"n": 0}

                        def demote(match):
                            seen["n"] += 1
                            if seen["n"] == 1:
                                return match.group(0)
                            tag = re.sub(r"^<h1\b", "<h2", match.group(0), flags=re.I)
                            return re.sub(r"</h1>$", "</h2>", tag, flags=re.I)

                        return H1_RE.sub(demote, text)

                    file = ctx.edit(f.url, demote_extra_h1s, result)
                    result.notes.append(f"multiple_h1: {ctx.rel(file)} -> extra H1s demoted to H2")
            except FixError as e:
                result.skipped.append(f"{f.issue_id} {path}: {e}")
        return result

    def suggest(self, finding):
        if finding.issue_id == "missing_h1":
            return (f"Add one H1 that names the page topic near the top of the main content of {finding.url}:\n"
                    "    <h1>Descriptive page heading</h1>")
        return (f"{finding.url} has several H1s. Keep one for the main topic and change the others to <h2>.")
