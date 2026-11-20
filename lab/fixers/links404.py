"""status_4xx -> fix the links that point at the broken URL.

The finding names the broken URL, not the page to edit. The Client Error (4xx) Inlinks export tells us
which source pages link to it. For each such link we either repoint it to a replacement chosen in
lab/fix_config.json ("link_fixes") or, when no replacement is configured, unwrap the link (keep the text).
"""
from __future__ import annotations

import re

from .base import FixError, Fixer, FixResult, esc

ANCHOR_RE = re.compile(r"<a\b([^>]*?)\bhref=([\"'])(.*?)\2([^>]*)>(.*?)</a>", re.I | re.S)


class Broken4xxFixer(Fixer):
    name = "links404"
    issue_ids = ("status_4xx",)

    def apply(self, ctx, findings):
        result = FixResult()
        replacements = ctx.config.get("link_fixes", {})
        for f in findings:
            broken_path = ctx.path_of(f.url)
            sources = sorted({row["Source"] for row in ctx.inlinks if row.get("Destination") == f.url})
            if not sources:
                result.skipped.append(f"status_4xx {broken_path}: no page links to it, so add a 301 or restore the page")
                continue
            target = replacements.get(broken_path)

            def transform(text, broken_path=broken_path, f=f, target=target):
                def repl(m):
                    if m.group(3) not in (broken_path, f.url):
                        return m.group(0)
                    if target:
                        return f"<a{m.group(1)}href={m.group(2)}{esc(target)}{m.group(2)}{m.group(4)}>{m.group(5)}</a>"
                    return m.group(5)  # unwrap: keep the visible text, drop the dead link
                return ANCHOR_RE.sub(repl, text)

            for source in sources:
                try:
                    file = ctx.edit(source, transform, result)
                    action = f"repointed to {target}" if target else "link removed (text kept)"
                    result.notes.append(f"status_4xx: {ctx.rel(file)} link to {broken_path} {action}")
                except FixError as e:
                    result.skipped.append(f"status_4xx {broken_path} linked from {ctx.path_of(source)}: {e}")
        return result

    def suggest(self, finding):
        return (f"{finding.url} returns an error. Restore it, or 301 it to the closest relevant live page, "
                "and update the internal links that point at it (find them under Inlinks in the crawl).")
